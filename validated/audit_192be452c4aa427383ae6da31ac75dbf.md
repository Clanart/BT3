### Title
Identity-key Schnorr signatures verify unconditionally through the generic deserialization path - (File: crypto/schnorr/src/lib.rs)

### Summary

`SchnorrSignature::read` accepts the group identity as `R`, and `SchnorrSignature::verify` does not reject either an identity public key or identity nonce commitment. Consequently, the signature `(R = identity, s = 0)` verifies for `public_key = identity` under every challenge. This is an authentication-bypass analog: identity is admitted as an alternate “key” through the canonical decoding path rather than being rejected before signature verification.

### Finding Description

`Ciphersuite::read_G` checks only that a point decodes and round-trips canonically; it does not reject the identity point. [1](#0-0)  `SchnorrSignature::read` uses that generic decoder for `R`, so an encoding of the identity point followed by a canonical zero scalar is accepted as a signature. [2](#0-1) 

Verification evaluates `R + cA - sG`. [3](#0-2)  With `A = identity`, `R = identity`, and `s = 0`, this expression is `identity + c·identity - 0·G`, which is identity for every challenge. [4](#0-3) 

The stricter identity rejection exists only in the FROST-specific `Curve::read_G` wrapper. [5](#0-4)  Callers using the generic `Ciphersuite` Schnorr API therefore have an alternate path that accepts the degenerate identity credential.

### Impact Explanation

An unprivileged caller can forge a valid Schnorr signature for the identity public key without knowing a discrete logarithm. If identity reaches the verifier through untrusted public-key bytes, or is otherwise accepted as an authentication key, the forged signature bypasses proof-of-possession/authentication for that key.

This does not forge signatures for non-identity keys. The impact is therefore limited to protocols that allow the identity point to function as a public key or fail to reject it before calling `verify`.

### Likelihood Explanation

The attack is deterministic once an identity public key reaches `SchnorrSignature::verify`. No hash collision, malformed encoding, privileged access, or secret material is required.

The condition is protocol-dependent, but the relevant deserializer explicitly accepts identity, so an attacker-controlled public-key field can supply the required key in implementations that do not impose an additional non-identity check.

### Recommendation

Reject identity points wherever a standalone Schnorr public key or nonce commitment is deserialized or accepted. In particular, `SchnorrSignature::read` should reject `R = identity`, and callers should reject `public_key = identity`; alternatively, add a strict Schnorr-specific decoder analogous to `Curve::read_G`.

### Proof of Concept

```rust
use zeroize::Zeroizing;
use ciphersuite::{group::Group, Ciphersuite};
use schnorr::SchnorrSignature;

fn forge_identity_signature<C: Ciphersuite>() -> bool {
  let public_key = C::G::identity();
  let forged = SchnorrSignature::<C> {
    R: C::G::identity(),
    s: C::F::ZERO,
  };

  // Holds for every challenge because:
  // identity + challenge * identity - 0 * generator == identity
  forged.verify(public_key, C::F::random(&mut rand_core::OsRng))
}
```

The serialized equivalent is the canonical encoding of `C::G::identity()` followed by the canonical encoding of `C::F::ZERO`. `SchnorrSignature::read` accepts both fields, after which `verify(identity, challenge)` returns true.

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

**File:** crypto/schnorr/src/lib.rs (L50-53)
```rust
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
```

**File:** crypto/schnorr/src/lib.rs (L86-100)
```rust
  /// Return the series of pairs whose products sum to zero for a valid signature.
  /// This is intended to be used with a multiexp.
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
```

**File:** crypto/schnorr/src/lib.rs (L102-110)
```rust
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
