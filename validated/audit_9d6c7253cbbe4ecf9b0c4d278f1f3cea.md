### Title
Missing proof-of-possession on registered encryption key lets a participant hijack another's `enc_key` and disclose the victim's secret shares - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
CVE-2018-16539 is a disclosure bug: an incorrect access check lets a crafted input read data that should not be readable. In PedPoP, the equivalent is the per-participant long-term encryption key `enc_key` carried in `EncryptionKeyMessage`. It is registered and trusted without any proof-of-possession or binding to the sender's identity, so a malicious participant can register someone else's `enc_key` as their own. All shares destined for them are then encrypted to the victim's encryption key, letting the victim — or the attacker colluding via the key's owner — decrypt shares they were never authorized to read. This violates the DKG's per-recipient confidentiality of secret shares.

### Finding Description
`Encryption::new` generates a fresh `enc_key`/`enc_pub_key` pair, and `registration` wraps it into `EncryptionKeyMessage { msg, enc_key }` alongside the `Commitments`. [1](#0-0)  The only cryptographic authentication on this message is `Commitments.sig`, a Schnorr PoK over `commitments[0]` with challenge `challenge(context, l, R, cached_msg)`, where `cached_msg` contains only the serialized commitment points — `enc_key` is appended to the wire format *after* the signed data and is covered by nothing. [2](#0-1) [3](#0-2) 

On receipt, `verify_r1` calls `self.encryption.register(l, msg)`, which only asserts `l` hasn't registered before and inserts `msg.enc_key` into `enc_keys` — no uniqueness check against other participants' keys and no PoP. [4](#0-3)  `verify_r1` then batch-verifies the PoK signature only, over `msg.cached_msg`, so a copied `enc_key` passes all validation. [5](#0-4) 

When honest senders generate shares, `generate_secret_shares` → `Encryption::encrypt` uses `self.decryption.enc_keys[&participant]` as the ECDH recipient key. [6](#0-5)  The PoP inside each `EncryptedMessage` (`pop`) proves ownership of the *ephemeral* per-message key `k`, and the code comments acknowledge the danger of key co-option — but that mitigation covers only the per-message key, not `enc_key`. [7](#0-6)  Notably, `pop_challenge` binds `context`, `nonce`, `key`, `sender`, and `msg`, but not the intended recipient's encryption key or index. [8](#0-7) 

Attack flow: Eve, as DKG participant `e`, broadcasts her `EncryptionKeyMessage` with `enc_key` set equal to honest participant Alice's `enc_pub_key` (copied verbatim from Alice's broadcast message). `Decryption::register` accepts it. Every honest participant then encrypts the share `f_i(e)` — intended only for Eve — as `cipher(context, ecdh(k, Alice_enc_pub))`. Alice knows the corresponding private key, so she computes `ecdh(Alice_enc_priv, msg.key)` herself and ChaCha20-decrypts each of the n-1 ciphertexts addressed to Eve. Summing the decrypted scalars yields Eve's complete FROST secret share `secret_e`, plus per-sender shares that verify against public commitments.

### Impact Explanation
The secret shares in a Pedersen/FROST DKG are confidential data addressed to exactly one participant — the analog of files "not readable" by other parties. Missing access checking on `enc_key` lets a party cause every share meant for one participant to be readable by another key holder. The party controlling the copied key recovers the victim's entire `ThresholdKeys` secret share: `self.secret` accumulates exactly these decrypted shares in `calculate_share`. [9](#0-8)  A leaked secret share reduces the effective threshold security and, if the attacker also participates or compromises t-1 shares total, enables key reconstruction or signing as the victim's index. Secondarily, the victim's own `calculate_share` fails `from_repr`/verification on shares it cannot decrypt, and in `decrypt_with_proof` the blame machinery evaluates `enc_keys[&decryptor]` — which points at the copied key — so the honest victim (not the attacker) is identified as the faulty party, compounding the disclosure with an incorrect blame/slash outcome. [10](#0-9) [11](#0-10) 

### Likelihood Explanation
Reachable by any single DKG participant using only public protocol inputs: the attacker broadcasts a normally-formatted `EncryptionKeyMessage` in round 1, which is exactly an authenticated-channel message they are permitted to send. No collusion, no validator compromise, and no broken primitives are needed — the code path (`register` → `encrypt` → `decrypt`) performs the ECDH mechanically against whatever key was registered. The condition for success is merely that the attacker observes another participant's commitment message before publishing their own, which is inherent to a broadcast round. Severity is Medium rather than High because disclosure of a single share does not alone recover the group key, and the attacker (if distinct from the key's owner) needs cooperation with, or must themselves be, the owner of the copied `enc_key` to read anything — though the copy-victim framing and mis-blame still cause concrete harm.

### Recommendation
Bind `enc_key` to the participant, mirroring the existing protections:
1. Include `enc_key` in the round-1 PoK scope — either by extending `challenge(context, l, R, Am)` to also cover the encryption key, or by appending `enc_key` bytes into `cached_msg` before signing so `Commitments.sig` proves knowledge of the polynomial secret over a message that commits to the encryption key.
2. Add a Schnorr proof-of-possession on `enc_key` (challenge binding `context` and `l`), verified in `verify_r1` alongside the commitments PoK.
3. Reject duplicate `enc_key` values across participants in `Decryption::register`/`verify_r1`, i.e., track claimed keys and error if two participants register the same point.
4. Optionally bind the intended recipient into `pop_challenge` so ciphertexts are non-reassignable across participants.

### Proof of Concept
```rust
// Setup: t-of-n PedPoP run, participants 1..=n. Eve is participant E, victim is A.
// Round 1: all parties broadcast EncryptionKeyMessage { Commitments, enc_key }.
let (eve_machine, mut eve_msg) = eve_keygen.generate_coefficients(&mut rng);
let (alice_machine, alice_msg) = alice_keygen.generate_coefficients(&mut rng);

// Attack: Eve copies Alice's enc_key into her own message.
// EncryptionKeyMessage.enc_key is private in-crate; on the wire it is simply the
// trailing C::G encoding after Commitments, so Eve serializes:
//   EveCommitments.write() || alice_msg.enc_key.to_bytes()
// i.e., a perfectly well-formed EncryptionKeyMessage under Eve's authenticated
// channel identity carrying Alice's encryption public key.
let mut wire = vec![];
eve_msg.msg.write(&mut wire).unwrap();
wire.extend(alice_msg.enc_key.to_bytes());
let eve_msg = EncryptionKeyMessage::<C, Commitments<C>>::read(&mut wire.as_slice(), params).unwrap();

// Round 2: every honest participant i computes shares for Eve encrypted to
//   ecdh(k_i, enc_keys[E]) where enc_keys[E] == Alice's enc_pub_key.
// In verify_r1, only the commitments PoK (over cached_msg, which excludes
// enc_key) is verified, so the co-opted key registers cleanly.

// Disclosure: Alice observes the EncryptedMessage sent to Eve (public/channel
// traffic or blame material) and computes the cipher key directly:
let shared = ecdh::<C>(&alice_enc_priv, msg_to_eve.key); // == sender's ecdh(k, A_pub)
cipher::<C>(context, &shared).apply_keystream(&mut msg_to_eve.msg.as_mut());
// msg_to_eve.msg now reveals f_i(E)'s serialized SecretShare. Repeating for all
// senders and summing yields Eve's full threshold secret share.

// Bonus mis-blame: Eve's calculate_share sees an invalid share (she can't
// decrypt under her real key), produces a blame proof, but decrypt_with_proof
// checks against enc_keys[E] = A_pub, so the DLEq fails and blame_internal
// returns `recipient` — honest Eve is blamed, not the sender nor the attack.
```

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-64)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    self.msg.write(writer)?;
    writer.write_all(self.enc_key.to_bytes().as_ref())
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L83-91)
```rust
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
  pop: SchnorrSignature<C>,
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L302-324)
```rust
fn pop_challenge<C: Ciphersuite>(
  context: [u8; 32],
  nonce: C::G,
  key: C::G,
  sender: Participant,
  msg: &[u8],
) -> C::F {
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption Key Proof of Possession v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"proof_of_possession");

  transcript.append_message(b"nonce", nonce.to_bytes());
  transcript.append_message(b"key", key.to_bytes());
  // This is sufficient to prevent the attack this is meant to stop
  transcript.append_message(b"sender", sender.to_bytes());
  // This, as written above, doesn't hurt
  transcript.append_message(b"message", msg);
  // While this is a PoK and a PoP, it's called a PoP here since the important part is its owner
  // Elsewhere, where we use the term PoK, the important part is that it isn't some inverse, with
  // an unknown to anyone discrete log, breaking the system
  C::hash_to_F(b"DKG-encryption-proof_of_possession", &transcript.challenge(b"schnorr"))
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-362)
```rust
  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    assert!(
      !self.enc_keys.contains_key(&participant),
      "Re-registering encryption key for a participant"
    );
    self.enc_keys.insert(participant, msg.enc_key);
    msg.msg
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-396)
```rust
    if let Some(proof) = proof {
      // Verify this is the decryption key for this message
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;

      cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
      Ok(msg.msg)
    } else {
      Err(DecryptionError::InvalidProof)
    }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L432-450)
```rust
impl<C: Ciphersuite> Encryption<C> {
  pub(crate) fn new<R: RngCore + CryptoRng>(
    context: [u8; 32],
    i: Participant,
    rng: &mut R,
  ) -> Self {
    let enc_key = Zeroizing::new(C::random_nonzero_F(rng));
    Self {
      context,
      i,
      enc_pub_key: C::generator() * enc_key.deref(),
      enc_key,
      decryption: Decryption::new(context),
    }
  }

  pub(crate) fn registration<M: Message>(&self, msg: M) -> EncryptionKeyMessage<C, M> {
    EncryptionKeyMessage { msg, enc_key: self.enc_pub_key }
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L460-467)
```rust
  pub(crate) fn encrypt<R: RngCore + CryptoRng, E: Encryptable>(
    &self,
    rng: &mut R,
    participant: Participant,
    msg: Zeroizing<E>,
  ) -> EncryptedMessage<C, E> {
    encrypt(rng, self.context, self.i, self.decryption.enc_keys[&participant], msg)
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L109-128)
```rust
impl<C: Ciphersuite> ReadWrite for Commitments<C> {
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L313-334)
```rust
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);

      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;
```

**File:** crypto/dkg/pedpop/src/lib.rs (L476-484)
```rust
    for (l, share_bytes) in shares.drain() {
      let (mut share_bytes, blame) =
        self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
      let share =
        Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
          PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
        })?);
      share_bytes.zeroize();
      *self.secret += share.deref();
```

**File:** crypto/dkg/pedpop/src/lib.rs (L575-608)
```rust
  fn blame_internal(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
      Ok(share_bytes) => share_bytes,
      // If there's an invalid signature, the sender did not send a properly formed message
      Err(DecryptionError::InvalidSignature) => return sender,
      // Decryption will fail if the provided ECDH key wasn't correct for the given message
      Err(DecryptionError::InvalidProof) => return recipient,
    };

    let Some(share) = Option::<C::F>::from(C::F::from_repr(share_bytes.0)) else {
      // If this isn't a valid scalar, the sender is faulty
      return sender;
    };

    // If this isn't a valid share, the sender is faulty
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
    }

    // The share was canonical and valid
    recipient
```
