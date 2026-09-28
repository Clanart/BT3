### Title
Schnorr verification accepts signatures for the identity public key, enabling trivial forgery - (File: crypto/schnorr/src/lib.rs)

### Summary

`SchnorrSignature::verify` does not reject the identity point as a public key. Since the verification equation is `R + cA - sG == 0`, setting `A` to identity removes the private-key term entirely. An attacker can then choose any scalar `s`, set `R = sG`, compute the protocol's normal challenge over `(R, A, message)`, and submit `(R, s)` as a valid signature. This affects signatures supplied through `SchnorrSignature::read` or verification APIs accepting an untrusted public key.

### Finding Description

`SchnorrSignature::verify` evaluates `batch_statements(public_key, challenge)`, which constructs the equation `R + challenge * public_key - s * generator == 0`. [1](#0-0)  Neither `verify` nor `batch_verify` checks whether `public_key` is the identity element. [2](#0-1) 

Deserialization does not provide this protection either: `SchnorrSignature::read` accepts any canonical point returned by `C::read_G`, while `Ciphersuite::read_G` checks only point validity and canonical encoding, not identity. [3](#0-2) [4](#0-3) 

This contrasts with FROST's point parser, which explicitly rejects identity points after canonical decoding. [5](#0-4) 

### Impact Explanation

An unprivileged party can forge a Schnorr signature under the identity public key for any message. The forged signature is not dependent on a malformed challenge: the challenge can correctly bind the public key, nonce commitment, and message as required by the API. Identity acceptance therefore permits authentication or authorization checks to be bypassed in any protocol that treats an attacker-supplied canonical point as a Schnorr verification key.

The most direct reachable path is attacker-controlled signature bytes passed to `SchnorrSignature::read`, followed by verification against an attacker-controlled or otherwise accepted identity public key.

### Likelihood Explanation

The attack requires a protocol to accept an identity point as a public key rather than obtaining a nonzero key from a trusted key-generation or identity subsystem. `Ciphersuite::read_G` permits that encoding, so serialization paths do not inherently prevent it. Exploitation is deterministic and requires no knowledge of a private key, but the impact is limited to protocols whose trust model allows attacker-selected public keys or which can be induced to verify against identity.

### Recommendation

Reject identity points for `public_key` in both `SchnorrSignature::verify` and `SchnorrSignature::batch_verify`. Rejecting identity `R` values as defense-in-depth is also reasonable, although the concrete forgery here relies on an identity public key. Protocol-level readers accepting public keys should use a non-identity point parser, equivalent to `Curve::read_G`, rather than the generic `Ciphersuite::read_G` unless identity is explicitly valid for that protocol.

### Proof of Concept

```rust
use ciphersuite::{Ciphersuite, group::Group};
use schnorr::SchnorrSignature;

fn forge_for_identity<C: Ciphersuite>(challenge: impl Fn(C::G, C::G, &[u8]) -> C::F, msg: &[u8]) {
    let public_key = C::G::identity();

    // Select s first, then set R = sG.
    let s = C::F::ONE;
    let r = C::generator() * s;
    let signature = SchnorrSignature::<C> { R: r, s };

    // The protocol's normal challenge is computed over the same R, A, and message.
    let c = challenge(r, public_key, msg);

    // Verification checks:
    //   R + cA - sG = sG + c * identity - sG = identity
    assert!(signature.verify(public_key, c));
}
```

### Citations

**File:** crypto/schnorr/src/lib.rs (L50-58)
```rust
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }

  /// Write a SchnorrSignature to something implementing Read.
  pub fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.R.to_bytes().as_ref())?;
    writer.write_all(self.s.to_repr().as_ref())
```

**File:** crypto/schnorr/src/lib.rs (L88-125)
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

  /// Queue a signature for batch verification.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  pub fn batch_verify<R: RngCore + CryptoRng, I: Copy + Zeroize>(
    &self,
    rng: &mut R,
    batch: &mut BatchVerifier<I, C::G>,
    id: I,
    public_key: C::G,
    challenge: C::F,
  ) {
    batch.queue(rng, id, self.batch_statements(public_key, challenge));
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

**File:** crypto/frost/src/curve/mod.rs (L123-130)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
```
