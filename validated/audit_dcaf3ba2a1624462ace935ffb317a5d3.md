### Title
Identity Schnorr public keys enable universally forged signatures - (File: `crypto/schnorr/src/lib.rs`)

### Summary
`Ciphersuite::read_G` accepts any canonical group encoding and does not reject the identity point. [1](#0-0)  `SchnorrSignature::read` directly uses that permissive point decoder for `R` and does not reject an identity nonce commitment. [2](#0-1)  Verification then checks only the equation `R + cA - sG = 0`, so `(R = identity, s = 0, A = identity)` verifies for every challenge. [3](#0-2) 

### Finding Description
The vulnerable input is fully public and deserializable: an attacker supplies a canonical identity point as the Schnorr public key and a serialized signature whose `R` is also the identity point and whose `s` is zero. [1](#0-0)  `batch_statements` reduces this input to `identity + c * identity - 0 * G`, which is identity regardless of `c`. [4](#0-3)  Consequently, `verify` returns true for a signature under a public key that has no corresponding private key. [5](#0-4) 

### Impact Explanation
This is a forged-signature primitive analogous to accepting `ecrecover(...) == address(0)`: the verifier accepts an invalid/null identity as an authenticated signer. [6](#0-5)  Any protocol path that treats a caller-supplied identity public key as an account, default key, placeholder, or recovered signer can be bypassed without knowledge of a secret. [1](#0-0) 

### Likelihood Explanation
The attack requires no private key, malformed encoding, race, or negligible-probability value because both required encodings are canonical. [1](#0-0)  It is exploitable where untrusted key and signature bytes are decoded with Serai’s public `read_G`/`SchnorrSignature::read` APIs and then passed to `verify`. [2](#0-1)  If the public key is fixed and already validated as non-identity elsewhere, this particular path is not reachable. [5](#0-4) 

### Recommendation
Reject identity points when deserializing Schnorr public keys and signatures, or reject identity `public_key` and `R` values at the start of `verify`. [2](#0-1)  The identity check used by FROST’s `Curve::read_G` is the appropriate semantic validation pattern for attacker-controlled points. [7](#0-6) 

### Proof of Concept
```rust
use std_shims::io::Cursor;

use ciphersuite::{
  group::{ff::{Field, PrimeField}, Group, GroupEncoding},
  Ciphersuite,
};
use dalek_ff_group::{Ristretto, Scalar};
use schnorr::SchnorrSignature;

let identity = <Ristretto as Ciphersuite>::G::identity();

let key_bytes = identity.to_bytes();
let public_key = Ristretto::read_G(&mut Cursor::new(key_bytes.as_ref())).unwrap();

let mut signature_bytes = identity.to_bytes().as_ref().to_vec();
signature_bytes.extend(Scalar::ZERO.to_repr().as_ref());
let signature =
  SchnorrSignature::<Ristretto>::read(&mut Cursor::new(signature_bytes)).unwrap();

assert!(signature.verify(public_key, Scalar::ONE));
```

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
