### Title

Identity encryption keys disable PedPoP share confidentiality - (File: `crypto/dkg/pedpop/src/encryption.rs`)

### Summary

PedPoP accepts the identity point as a participant’s encryption public key. Shares encrypted to that participant use the publicly known ECDH result `k * identity = identity`, allowing any observer of the authenticated DKG channel to derive the ChaCha20 keystream and recover the participant’s threshold key share.

### Finding Description

`EncryptionKeyMessage::read` deserializes `enc_key` with the generic `Ciphersuite::read_G`, which validates canonical point encoding but does not reject the identity point. [1](#0-0) [2](#0-1) 

`SecretShareMachine::verify_r1` authenticates the embedded coefficient commitments and their proof of knowledge, but `register` stores the attacker-controlled `enc_key` without an identity or subgroup-level validity check. [3](#0-2) [4](#0-3) 

When shares are generated, `Encryption::encrypt` passes that registered key to `encrypt`; the resulting ECDH value is `public * private`. If `public` is the identity, the shared point is always the identity, regardless of the random per-message key. [5](#0-4) [6](#0-5) [7](#0-6) 

The cipher derives its ChaCha20 key solely from the public DKG context and the encoded ECDH point, with a fixed IV. For an identity ECDH result, all inputs are public and the keystream is therefore publicly computable. [8](#0-7) 

### Impact Explanation

Any party who can observe the authenticated DKG messages can recover every secret share addressed to a participant that registered an identity encryption key. Those shares define that participant’s final `ThresholdKeys` secret share because `calculate_share` sums the decrypted sender shares into `self.secret`. [9](#0-8) 

This breaks the confidentiality property that the per-message ECDH encryption is intended to provide. A single recovered share is not sufficient by itself to control a `t`-of-`n` key, but it is concrete threshold-key-share recovery and reduces the attack surface to obtaining the remaining shares through a separate compromise or protocol failure.

### Likelihood Explanation

An unprivileged DKG participant can supply the malicious encoding as part of its ordinary round-one message. The message can contain otherwise valid coefficient commitments and a valid proof of knowledge because that proof covers the commitments, while `enc_key` is only appended afterward and is not required to be non-identity. [10](#0-9) [11](#0-10) 

The attack requires the attacker to participate in the DKG and the observer to see ciphertexts addressed to the attacker. Those ciphertexts are explicitly encrypted rather than relying on channel confidentiality, so observation by relays, coordinators, or archival logs is within the protocol’s threat surface.

### Recommendation

Reject identity points for all PedPoP encryption public keys before registration and when deserializing them:

- Check `enc_key.is_identity()` in `EncryptionKeyMessage::read` or immediately in `Decryption::register`.
- Apply the same check to `EncryptedMessage::key` and `EncryptionKeyProof::key`, unless a protocol explicitly permits identity there.
- Prefer a non-identity point decoder for these fields, matching the behavior of `Curve::read_G`, which rejects identity points. [12](#0-11) 
- Add a regression test in which an otherwise valid commitment message carries an identity `enc_key` and must be rejected.

### Proof of Concept

The attacker produces a normal round-one message, then replaces only its trailing `enc_key` encoding with the identity point before sending it:

```rust
use ciphersuite::{group::GroupEncoding, Group};
use dkg_pedpop::{Commitments, EncryptionKeyMessage, KeyGenMachine};

// Attacker participant j creates valid commitments and PoK.
let (_, msg) = KeyGenMachine::<C>::new(attacker_params, context)
  .generate_coefficients(&mut rng);

// EncryptionKeyMessage serializes msg followed by enc_key.
let mut bytes = msg.serialize();
let enc_len = <C::G as GroupEncoding>::Repr::default().as_ref().len();
let enc_start = bytes.len() - enc_len;
bytes[enc_start..].copy_from_slice(C::G::identity().to_bytes().as_ref());

// This succeeds because C::read_G accepts canonical identity encodings.
let malicious =
  EncryptionKeyMessage::<C, Commitments<C>>::read(&mut &bytes[..], attacker_params)
    .unwrap();
```

An honest participant accepts `malicious`, registers the identity encryption key, and produces a share message whose cipher is derived from `identity`. An observer reconstructs the same ChaCha20 keystream from `context` and the identity encoding, then XORs it with the serialized encrypted share to recover the threshold share plaintext.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-59)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L61-64)
```rust
  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    self.msg.write(writer)?;
    writer.write_all(self.enc_key.to_bytes().as_ref())
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-97)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-132)
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
  zeroize(key.as_mut());
  res
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

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```

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

**File:** crypto/dkg/pedpop/src/lib.rs (L311-329)
```rust
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L474-491)
```rust
    let mut batch = BatchVerifier::new(shares.len());
    let mut blames = HashMap::new();
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

**File:** crypto/frost/src/curve/mod.rs (L123-131)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```
