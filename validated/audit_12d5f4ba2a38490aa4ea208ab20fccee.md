### Title
PedPoP encrypted messages accept identity keys with universally valid Schnorr proofs - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary

`EncryptedMessage::read` accepts the canonical identity point as the per-message encryption key because it calls `Ciphersuite::read_G`, which validates only point decoding and canonicality. It also accepts a Schnorr proof whose nonce commitment `R` is identity and whose response `s` is zero. `SchnorrSignature::verify` then evaluates `R + cA - sG`; when both `R` and `A` are identity, the statement is identity for every challenge. An unauthenticated DKG message can therefore carry an identity encryption key with a universally accepted proof of possession, despite honest generation requiring a non-zero ephemeral key. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description

`EncryptedMessage::read` deserializes three attacker-controlled fields: `key`, `pop`, and `msg`. The point reader rejects invalid or non-canonical encodings, but not identity. The Schnorr reader similarly deserializes `R` and `s` without rejecting `R = identity`. [2](#0-1) [4](#0-3) 

For Ristretto, the attacker can encode:

- `key = identity`
- `pop.R = identity`
- `pop.s = 0`

The resulting verification statement is `identity + c * identity - 0 * G = identity`, so the signature verifies regardless of the transcript challenge or ciphertext. [3](#0-2) 

The malformed object reaches the decryption path in `Encryption::decrypt`, which queues the proof for batch verification and derives the ECDH point from `msg.key`. [5](#0-4)  `KeyMachine::calculate_share` subsequently verifies the queued proof and share statements, allowing a properly constructed ciphertext/share pair to proceed through normal DKG share processing. [6](#0-5) 

This violates the protocol’s own key-generation invariant: honest encryption uses `random_nonzero_F`, so an honest implementation can never emit an identity encryption key. [7](#0-6) 

### Impact Explanation

A crafted public DKG share message can contain a forged-looking but universally accepted Schnorr proof for an identity encryption key. This bypasses the intended domain restriction that the per-message encryption key is generated from a non-zero scalar.

The identity key also makes the ECDH result identity: `ecdh(recipient_private, identity) = identity`. Consequently, the ChaCha20 key is derived from a public, fixed point rather than from a sender-generated ephemeral secret. [8](#0-7)  The sender can therefore construct ciphertext offline, and any observer who can derive the same transcript can decrypt the payload. If the decrypted share is valid relative to the sender’s commitments, the malformed message is accepted by the DKG; if invalid, normal share validation and blame still apply. [9](#0-8) 

The concrete security violation is acceptance of a proof for an out-of-domain key and the resulting use of a known ECDH shared point for secret-share decryption.

### Likelihood Explanation

Any party able to submit a PedPoP encrypted share message can construct the encoding deterministically. No discrete-log knowledge, secret key, timing condition, or collision is required. For Ristretto, identity is represented by 32 zero bytes and the zero scalar is another 32 zero bytes. The attack requires only placing those encodings in the `key` and `pop.R`/`pop.s` positions and deriving the fixed identity-key keystream for the encrypted share payload.

### Recommendation

Reject identity in all protocol objects where identity is not a legitimate public value:

- In `EncryptedMessage::read`, reject `key.is_identity()`.
- In `SchnorrSignature::read` or callers processing proofs of possession, reject `signature.R.is_identity()`.
- In `Commitments::read`, reject identity commitment points, since honest PedPoP commitments are generated from non-zero coefficients.
- Prefer a dedicated `read_nonzero_G` helper on `Ciphersuite` for public keys, commitments, nonce commitments, and other points that must not be identity.
- Add regression tests using `key = identity`, `R = identity`, and `s = 0` for both commitment messages and encrypted shares.

### Proof of Concept

For `C = Ristretto`, construct a serialized encrypted message as:

```rust
let mut encoded = Vec::new();
encoded.extend_from_slice(&[0; 32]); // key: canonical Ristretto identity
encoded.extend_from_slice(&[0; 32]); // pop.R: canonical Ristretto identity
encoded.extend_from_slice(&[0; 32]); // pop.s: zero scalar
encoded.extend_from_slice(&ciphertext); // fixed-width SecretShare ciphertext

let msg = EncryptedMessage::<Ristretto, SecretShare<Scalar>>::read(
    &mut encoded.as_slice(),
    params,
)?;
```

`msg.pop.verify(msg.key, challenge)` returns true for every challenge because all three multiexponentiation terms are identity or zero. To make the decrypted scalar chosen by the sender, compute the same ChaCha20 keystream used by `cipher(context, identity)` and XOR it with the desired canonical `SecretShare` representation before placing the result in `ciphertext`.

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-101)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}

// Each ecdh must be distinct. Reuse of an ecdh for multiple ciphers will cause the messages to be
// leaked.
fn cipher<C: Ciphersuite>(context: [u8; 32], ecdh: &Zeroizing<C::G>) -> ChaCha20 {
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L151-164)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-176)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L479-489)
```rust
    msg.pop.batch_verify(
      rng,
      batch,
      batch_id,
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    );

    let key = ecdh::<C>(&self.enc_key, msg.key);
    cipher::<C>(self.context, &key).apply_keystream(msg.msg.as_mut().as_mut());
    (
```

**File:** crypto/schnorr/src/lib.rs (L50-59)
```rust
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }

  /// Write a SchnorrSignature to something implementing Read.
  pub fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.R.to_bytes().as_ref())?;
    writer.write_all(self.s.to_repr().as_ref())
  }
```

**File:** crypto/schnorr/src/lib.rs (L88-110)
```rust
  pub fn batch_statements(&self, public_key: C::G, challenge: C::F) -> [(C::F, C::G); 3] {
    // s = r + ca
    // sG == R + cA
    // R + cA - sG == 0
    [
      // R
      (C::F::ONE, self.R),
      // cA
      (challenge, public_key),
      // -sG
      (-self.s, C::generator()),
    ]
  }

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L474-499)
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
    }
    batch.verify_with_vartime_blame().map_err(|id| {
      let (l, blame) = match id {
        BatchId::Decryption(l) => (l, None),
        BatchId::Share(l) => (l, Some(blames.remove(&l).unwrap())),
      };
      PedPoPError::InvalidShare { participant: l, blame }
    })?;
```
