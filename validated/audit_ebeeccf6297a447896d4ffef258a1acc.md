### Title
Encrypted DKG messages accept a forged proof of possession for the identity encryption key - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
`EncryptedMessage::read` accepts an identity public encryption key because `Ciphersuite::read_G` only enforces a canonical point encoding and does not reject identity. The associated Schnorr proof of possession can then be forged as `(R = identity, s = 0)`, because the verifier equation becomes `identity + challenge * identity - 0 * G = identity`. This bypasses the possession check intended to prevent an attacker from claiming use of an encryption key they do not control.

### Finding Description
`EncryptedMessage` consists of an ephemeral public `key`, a Schnorr `pop`, and encrypted `msg` bytes. [1](#0-0)  The PoP is specifically required so an attacker cannot claim another party's encryption key or otherwise assert use of a key without knowing its discrete logarithm. [2](#0-1)  However, `Ciphersuite::read_G` accepts any canonical group element, including the identity. [3](#0-2) 

During decryption, `msg.pop` is batch-verified under `msg.key` and a challenge committing to the context, nonce, key, sender, and ciphertext. [4](#0-3)  `SchnorrSignature::verify` checks `R + challenge * public_key - s * G == identity`. [5](#0-4)  If `public_key`, `R`, and `s` are all identity/zero, this equation always holds for every challenge.

The forged message also uses a predictable ECDH output: multiplication of the recipient's private key by the identity key produces identity, so the attacker knows the ChaCha20 key derived from that ECDH result and the static IV. [6](#0-5) 

### Impact Explanation
An unauthenticated byte string can carry a forged encryption-key proof of possession. The attacker can construct an `EncryptedMessage` with `key = identity`, `pop = { R: identity, s: 0 }`, and ciphertext encrypted under the publicly known identity-derived cipher. The message passes the authorization check despite the sender not possessing a corresponding nonzero ephemeral private key.

The immediate practical consequence is arbitrary ciphertext injection into `calculate_share`. Later share-validation should reject plaintext that does not satisfy the sender's commitments, so this does not by itself provide a valid threshold share. It still produces a forged proof accepted by a security-critical API and bypasses the check designed to prevent encryption-key misuse and blame-protocol abuse.

### Likelihood Explanation
The input is fully attacker-controlled and consists only of canonical encodings: the identity point for `key`, the identity point for `pop.R`, scalar zero for `pop.s`, and ciphertext generated with the known identity ECDH key. No discrete logarithm, private state, malformed encoding, race, or probabilistic condition is required.

`Curve::read_G` rejects identity for FROST inputs, but PedPoP uses the more general `Ciphersuite::read_G`, which performs canonicality checks without an identity rejection. [7](#0-6) [3](#0-2) 

### Recommendation
Reject identity keys before accepting an `EncryptedMessage`. The strongest fix is to reject `msg.key.is_identity()` in `EncryptedMessage::read`, and preferably also reject identity `R` values when parsing `SchnorrSignature`. Additionally, `SchnorrSignature::verify` should reject identity public keys and nonce commitments at the verifier boundary unless explicitly justified for a specialized internal use.

### Proof of Concept
For any supported `Ciphersuite` `C` and fixed threshold parameters:

```rust
use ciphersuite::{group::{ff::Field, Group}, Ciphersuite};
use dkg_pedpop::encryption::*;
use schnorr::SchnorrSignature;

// Conceptual construction of attacker-controlled EncryptedMessage bytes:
//
// key      = C::G::identity().to_bytes()
// pop.R    = C::G::identity().to_bytes()
// pop.s    = C::F::ZERO.to_repr()
// msg      = arbitrary bytes XOR ChaCha20(
//              key = transcript(context, identity_ECDH),
//              iv  = "DKG IV v0.2\0",
//            )
//
// EncryptedMessage::<C, E>::read accepts key because identity is canonical.
// Schnorr verification then computes:
//
//   R + c * key - s * G
// = identity + c * identity - 0 * G
// = identity
//
// Therefore every challenge value is accepted.
```

The root cause is the combination of accepting an identity ephemeral key and allowing the all-identity Schnorr equation to satisfy verification. [1](#0-0) [5](#0-4)

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-130)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}

// Each ecdh must be distinct. Reuse of an ecdh for multiple ciphers will cause the messages to be
// leaked.
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L469-485)
```rust
  pub(crate) fn decrypt<R: RngCore + CryptoRng, I: Copy + Zeroize, E: Encryptable>(
    &self,
    rng: &mut R,
    batch: &mut BatchVerifier<I, C::G>,
    // Uses a distinct batch ID so if this batch verifier is reused, we know its the PoP aspect
    // which failed, and therefore to use None for the blame
    batch_id: I,
    from: Participant,
    mut msg: EncryptedMessage<C, E>,
  ) -> (Zeroizing<E>, EncryptionKeyProof<C>) {
    msg.pop.batch_verify(
      rng,
      batch,
      batch_id,
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    );
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

**File:** crypto/schnorr/src/lib.rs (L88-109)
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
