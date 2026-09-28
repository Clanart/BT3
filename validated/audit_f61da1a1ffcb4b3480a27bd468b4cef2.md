### Title
Unauthenticated PedPoP encryption-key substitution leaks a participant’s DKG share - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary

`EncryptionKeyMessage` serializes the participant’s encryption key after the signed `Commitments`, but the Schnorr proof only commits to the commitment bytes, not to the appended key. [1](#0-0) [2](#0-1)  A party that substitutes a victim registration’s trailing encryption key therefore preserves the valid proof while causing all DKG shares intended for that participant index to be encrypted to the attacker’s key. [3](#0-2) [4](#0-3) 

### Finding Description

`Commitments::read` builds `cached_msg` exclusively from the encoded polynomial commitments and then reads the Schnorr signature. [5](#0-4)  The proof challenge binds the context, participant index, nonce, and `cached_msg`, but not the subsequently parsed `enc_key`. [6](#0-5) [7](#0-6) 

`verify_r1` registers the received encryption key and verifies the proof against only `msg.sig.R` and `msg.cached_msg`. [8](#0-7)  Consequently, the cryptographic check validates the unchanged commitment prefix while the protocol consumes a different, attacker-selected encryption key. [9](#0-8) [4](#0-3) 

### Impact Explanation

After substitution, `Encryption::encrypt` retrieves the attacker-controlled registered key for the target participant index and encrypts that participant’s share to it. [10](#0-9)  The attacker can compute the same ECDH point as `msg.key * attacker_scalar` and decrypt every share sent to the victim index. [11](#0-10) [12](#0-11) 

Because `KeyMachine::calculate_share` adds all decrypted contributions into the participant’s final secret share, observing those plaintext contributions lets the attacker reconstruct the victim’s final `ThresholdKeys` secret share. [13](#0-12) [14](#0-13)  This is a Medium/High severity key-share confidentiality violation depending on whether the attacker can combine the recovered share with other compromised shares. [15](#0-14) 

### Likelihood Explanation

Exploitation requires the ability to deliver or modify the victim participant’s public registration bytes before they are passed to `EncryptionKeyMessage::read`, such as through an untrusted relay, gossip path, or peer-supplied message cache. [2](#0-1)  It does not require knowledge of the victim’s secret share or the registered private encryption key because the attacker can choose an encryption key for which they know the discrete logarithm. [16](#0-15) [4](#0-3) 

### Recommendation

Bind `enc_key` to the PedPoP proof by including its canonical encoding in `cached_msg` and the `challenge` transcript, or by signing the complete serialized `EncryptionKeyMessage`. [6](#0-5) [2](#0-1)  Also require a proof of possession for the registered encryption private key, ensuring a substituted key cannot be used merely by copying another participant’s valid commitment proof. [17](#0-16) 

### Proof of Concept

```rust
// C is Ristretto or another PedPoP ciphersuite.
// victim_registration_bytes is a valid EncryptionKeyMessage for target `l`.
let mut wrapped = victim_registration_bytes;

// EncryptionKeyMessage serializes:
//   Commitments || SchnorrSignature || enc_key
// Replace only the final enc_key encoding.
let g_len = <C::G as GroupEncoding>::Repr::default().as_ref().len();
let enc_key_at = wrapped.len() - g_len;
wrapped[enc_key_at ..].copy_from_slice(
  (C::generator() * attacker_scalar).to_bytes().as_ref(),
);

let wrapped_msg =
  EncryptionKeyMessage::<C, Commitments<C>>::read(&mut wrapped.as_slice(), params)?;

// Insert this under the victim's participant index.
commitment_msgs.insert(victim_participant, wrapped_msg);

// verify_r1 still succeeds: Commitments::cached_msg and the proof bytes are
// unchanged, while register() stores attacker_public_key for the victim.
```

Each peer then calls `encrypt(..., victim_participant, share)`, which looks up the substituted key and encrypts the victim-index share to the attacker. [10](#0-9)  For every emitted `EncryptedMessage`, the attacker derives `shared = msg.key * attacker_scalar`, derives the documented ChaCha20 key from `context || shared`, decrypts the share, and sums all victim-index shares to obtain the victim’s final DKG secret share. [18](#0-17) [19](#0-18)

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L86-94)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L110-128)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L476-491)
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
      );
```

**File:** crypto/dkg/pedpop/src/lib.rs (L523-530)
```rust
    let KeyMachine { commitments, encryption, params, secret } = self;
    Ok(BlameMachine {
      commitments,
      encryption: encryption.into_decryption(),
      result: Some(
        ThresholdKeys::new(params, Interpolation::Lagrange, secret, verification_shares)
          .map_err(PedPoPError::DkgError)?,
      ),
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-64)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    self.msg.write(writer)?;
    writer.write_all(self.enc_key.to_bytes().as_ref())
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L78-92)
```rust
/// An encrypted message, with a per-message encryption key enabling revealing specific messages
/// without side effects.
#[derive(Clone, Zeroize)]
pub struct EncryptedMessage<C: Ciphersuite, E: Encryptable> {
  key: C::G,
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
  pop: SchnorrSignature<C>,
  msg: Zeroizing<E>,
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-97)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-130)
```rust
fn cipher<C: Ciphersuite>(context: [u8; 32], ecdh: &Zeroizing<C::G>) -> ChaCha20 {
  // Ideally, we'd box this transcript with ZAlloc, yet that's only possible on nightly
  // TODO: https://github.com/serai-dex/serai/issues/151
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"encryption_key");

  let mut ecdh = ecdh.to_bytes();
  transcript.append_message(b"shared_key", ecdh.as_ref());
  ecdh.as_mut().zeroize();

  let zeroize = |buf: &mut [u8]| buf.zeroize();

  let mut key = Cc20Key::default();
  let mut challenge = transcript.challenge(b"key");
  key.copy_from_slice(&challenge[.. 32]);
  zeroize(challenge.as_mut());

  // Since the key is single-use, it doesn't matter what we use for the IV
  // The issue is key + IV reuse. If we never reuse the key, we can't have the opportunity to
  // reuse a nonce
  // Use a static IV in acknowledgement of this
  let mut iv = Cc20Iv::default();
  // The \0 is to satisfy the length requirement (12), not to be null terminated
  iv.copy_from_slice(b"DKG IV v0.2\0");

  // ChaCha20 has the same commentary as the transcript regarding ZAlloc
  // TODO: https://github.com/serai-dex/serai/issues/151
  let res = ChaCha20::new(&key, &iv);
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L153-165)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L487-493)
```rust
    let key = ecdh::<C>(&self.enc_key, msg.key);
    cipher::<C>(self.context, &key).apply_keystream(msg.msg.as_mut().as_mut());
    (
      msg.msg,
      EncryptionKeyProof {
        key,
        dleq: DLEqProof::prove(
```
