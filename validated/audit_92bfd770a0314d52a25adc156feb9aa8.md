### Title
PedPoP participant commitments authenticate the claimed participant ID but leave the recipient encryption key unbound, enabling share-recipient impersonation - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
High. `EncryptionKeyMessage` carries a signed PedPoP `Commitments` payload plus a separate `enc_key`, but the Schnorr proof only commits to the context, participant index, nonce, and commitment bytes—not `enc_key`. [1](#0-0) [2](#0-1)  `SecretShareMachine::verify_r1` therefore accepts a valid commitment/PoK for participant `l` while registering whichever `enc_key` was supplied beside it. [3](#0-2) [4](#0-3)  An attacker can pair a victim’s valid signed commitment message with an attacker-controlled encryption key, causing dealers to encrypt the victim’s secret shares to the attacker. [5](#0-4) [6](#0-5) 

### Finding Description
`EncryptionKeyMessage::read` parses `M::read(...)` and then reads `enc_key` as trailing untrusted bytes, returning both as one message. [7](#0-6)  For `Commitments`, `Commitments::read` stores only the serialized commitment points in `cached_msg`; the encryption key is not included in that signed byte string. [8](#0-7)  The PoK challenge is `context || participant || R || commitments`, with no encryption-key field. [2](#0-1)  During round-one verification, the code registers `l -> msg.enc_key` before batch-verifying the commitment PoK under `l`, so a substitution of `enc_key` does not invalidate the proof. [9](#0-8) [4](#0-3)  Later, `generate_secret_shares` encrypts each participant’s polynomial share to the registered key for that participant index. [10](#0-9) [6](#0-5) 

### Impact Explanation
If a victim participant `v` broadcasts a valid `EncryptionKeyMessage<Commitments>`, an attacker can replay the victim’s signed commitment bytes while replacing the trailing `enc_key` with a public key whose private key the attacker controls. [7](#0-6) [11](#0-10)  The altered message still verifies for `v` because the signature challenge is unchanged by the encryption-key substitution. [2](#0-1) [12](#0-11)  Every honest dealer then encrypts `f_j(v)` to the attacker’s registered key; if the attacker obtains those ciphertexts, the attacker decrypts and sums the shares to recover the victim’s final threshold secret share. [13](#0-12) [14](#0-13) [15](#0-14)  Recovery of a threshold key share satisfies the key-share-recovery impact criterion.

### Likelihood Explanation
The attack only requires control over untrusted `EncryptionKeyMessage` bytes or the participant-key map supplied to `generate_secret_shares`, plus access to a victim’s prior valid commitment message for the same context. [16](#0-15) [7](#0-6)  No discrete-log break, invalid curve point, colluding threshold, or malicious local validator is required: the forged object is byte-level substitution of an unsigned field in an otherwise valid message. [2](#0-1) [4](#0-3) 

### Recommendation
Bind `enc_key` into the authenticated PedPoP commitment transcript by including `enc_key.to_bytes()` in `cached_msg`/the PoK challenge before verification, or add a separate proof of possession for `enc_key` whose challenge binds `context`, the participant index, the encryption key, and the commitment message. [2](#0-1) [17](#0-16)  `Encryption::register` should reject any encryption key not cryptographically bound to the same participant and context as the verified commitments. [4](#0-3) 

### Proof of Concept
1. Victim `v` runs `KeyGenMachine::generate_coefficients`, producing `EncryptionKeyMessage { msg: Commitments { .., sig_v }, enc_key: V_enc }`, where `sig_v` proves knowledge of `commitments[0]` under challenge `context || v || R || commitments`. [18](#0-17) 
2. Attacker preserves `msg` exactly but replaces `enc_key` with `A_enc = G * a`, yielding `EncryptionKeyMessage { msg: victim_msg, enc_key: A_enc }`. [1](#0-0) 
3. A dealer calls `SecretShareMachine::generate_secret_shares` with this message stored under `Participant(v)`; `verify_r1` registers `v -> A_enc` and accepts the still-valid `sig_v`. [19](#0-18) [4](#0-3) 
4. The dealer computes the victim’s polynomial share and calls `self.encryption.encrypt(rng, v, share_bytes)`, which encrypts it with ECDH to `A_enc`. [20](#0-19) [14](#0-13) 
5. The attacker decrypts the resulting `EncryptedMessage` using private key `a`, obtaining `f_dealer(v)`; collecting all dealers’ shares for index `v` recovers the victim’s final secret share `sum_j f_j(v)`. [15](#0-14) [21](#0-20)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L48-64)
```rust
/// Wraps a message with a key to use for encryption in the future.
#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]
pub struct EncryptionKeyMessage<C: Ciphersuite, M: Message> {
  msg: M,
  enc_key: C::G,
}

// Doesn't impl ReadWrite so that doesn't need to be imported
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    self.msg.write(writer)?;
    writer.write_all(self.enc_key.to_bytes().as_ref())
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L151-167)
```rust
  // Generate a new key for this message, satisfying cipher's requirement of distinct keys per
  // message, and enabling revealing this message without revealing any others
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());

  let pub_key = C::generator() * key.deref();
  let nonce = Zeroizing::new(C::random_nonzero_F(rng));
  let pub_nonce = C::generator() * nonce.deref();
  EncryptedMessage {
    key: pub_key,
    pop: SchnorrSignature::sign(
      &key,
      nonce,
      pop_challenge::<C>(context, pub_nonce, pub_key, from, msg.deref().as_ref()),
    ),
    msg,
  }
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-361)
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L487-499)
```rust
    let key = ecdh::<C>(&self.enc_key, msg.key);
    cipher::<C>(self.context, &key).apply_keystream(msg.msg.as_mut().as_mut());
    (
      msg.msg,
      EncryptionKeyProof {
        key,
        dleq: DLEqProof::prove(
          rng,
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &self.enc_key,
        ),
      },
```

**File:** crypto/dkg/pedpop/src/lib.rs (L85-94)
```rust
#[allow(non_snake_case)]
fn challenge<C: Ciphersuite>(context: [u8; 32], l: Participant, R: &[u8], Am: &[u8]) -> C::F {
  let mut transcript = RecommendedTranscript::new(b"DKG PedPoP v0.2");
  transcript.domain_separate(b"schnorr_proof_of_knowledge");
  transcript.append_message(b"context", context);
  transcript.append_message(b"participant", l.to_bytes());
  transcript.append_message(b"nonce", R);
  transcript.append_message(b"commitments", Am);
  C::hash_to_F(b"DKG-PedPoP-proof_of_knowledge-0", &transcript.challenge(b"schnorr"))
}
```

**File:** crypto/dkg/pedpop/src/lib.rs (L109-133)
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

  fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(&self.cached_msg)?;
    self.sig.write(writer)
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L156-191)
```rust
  pub fn generate_coefficients<R: RngCore + CryptoRng>(
    self,
    rng: &mut R,
  ) -> (SecretShareMachine<C>, EncryptionKeyMessage<C, Commitments<C>>) {
    let t = usize::from(self.params.t());
    let mut coefficients = Vec::with_capacity(t);
    let mut commitments = Vec::with_capacity(t);
    let mut cached_msg = vec![];

    for i in 0 .. t {
      // Step 1: Generate t random values to form a polynomial with
      coefficients.push(Zeroizing::new(C::random_nonzero_F(&mut *rng)));
      // Step 3: Generate public commitments
      commitments.push(C::generator() * coefficients[i].deref());
      cached_msg.extend(commitments[i].to_bytes().as_ref());
    }

    // Step 2: Provide a proof of knowledge
    let r = Zeroizing::new(C::random_nonzero_F(rng));
    let nonce = C::generator() * r.deref();
    let sig = SchnorrSignature::<C>::sign(
      &coefficients[0],
      // This could be deterministic as the PoK is a singleton never opened up to cooperative
      // discussion
      // There's no reason to spend the time and effort to make this deterministic besides a
      // general obsession with canonicity and determinism though
      r,
      challenge::<C>(self.context, self.params.i(), nonce.to_bytes().as_ref(), &cached_msg),
    );

    // Additionally create an encryption mechanism to protect the secret shares
    let encryption = Encryption::new(self.context, self.params.i(), rng);

    // Step 4: Broadcast
    let msg =
      encryption.registration(Commitments { commitments: commitments.clone(), cached_msg, sig });
```

**File:** crypto/dkg/pedpop/src/lib.rs (L303-334)
```rust
    mut commitment_msgs: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>,
  ) -> Result<HashMap<Participant, Vec<C::G>>, PedPoPError<C>> {
    validate_map(
      &commitment_msgs,
      &self.params.all_participant_indexes().collect::<Vec<_>>(),
      self.params.i(),
    )?;

    let mut batch = BatchVerifier::<Participant, C::G>::new(commitment_msgs.len());
    let mut commitments = HashMap::new();
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

**File:** crypto/dkg/pedpop/src/lib.rs (L347-355)
```rust
  pub fn generate_secret_shares<R: RngCore + CryptoRng>(
    mut self,
    rng: &mut R,
    commitments: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>,
  ) -> Result<
    (KeyMachine<C>, HashMap<Participant, EncryptedMessage<C, SecretShare<C::F>>>),
    PedPoPError<C>,
  > {
    let commitments = self.verify_r1(&mut *rng, commitments)?;
```

**File:** crypto/dkg/pedpop/src/lib.rs (L357-377)
```rust
    // Step 1: Generate secret shares for all other parties
    let mut res = HashMap::new();
    for l in self.params.all_participant_indexes() {
      // Don't insert our own shares to the byte buffer which is meant to be sent around
      // An app developer could accidentally send it. Best to keep this black boxed
      if l == self.params.i() {
        continue;
      }

      let mut share = polynomial(&self.coefficients, l);
      let share_bytes = Zeroizing::new(SecretShare::<C::F>(share.to_repr()));
      share.zeroize();
      res.insert(l, self.encryption.encrypt(rng, l, share_bytes));
    }

    // Calculate our own share
    let share = polynomial(&self.coefficients, self.params.i());
    self.coefficients.zeroize();

    Ok((
      KeyMachine { params: self.params, secret: share, commitments, encryption: self.encryption },
```

**File:** crypto/dkg/pedpop/src/lib.rs (L476-490)
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

      blames.insert(l, blame);
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
```
