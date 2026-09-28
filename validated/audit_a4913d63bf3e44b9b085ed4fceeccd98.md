### Title
Missing identity-key rejection permits trivial Schnorr signature forgery - (File: crypto/schnorr/src/lib.rs)

### Summary
Medium: `SchnorrSignature::verify` accepts the identity point as a public key, allowing an attacker to construct a universally valid signature without a private key. [1](#0-0) 

### Finding Description
`SchnorrSignature::read` accepts any canonically encoded `R` and scalar `s`, without rejecting an identity nonce. [2](#0-1)  Likewise, `Ciphersuite::read_G` validates decoding and canonical re-encoding but does not reject the identity group element. [3](#0-2) 

Verification evaluates `R + cA - sG == 0`. [4](#0-3)  If `A` is the identity, the `cA` term is always identity, so choosing `R = sG` satisfies the equation for every challenge `c` and every message.

### Impact Explanation
Any protocol path that treats an attacker-supplied point as the verification key can be bypassed with a forged signature under the identity key. This is a signature-forgery primitive rather than merely a malformed encoding: the attacker selects the canonical identity encoding as the public key and submits `(R, s) = (sG, s)` as the signature.

### Likelihood Explanation
The attack is reachable whenever untrusted public keys, commitments, or signature bytes are accepted without an independent non-identity public-key check. It does not let an attacker impersonate an already-trusted non-identity key, so exploitation depends on a protocol accepting an attacker-controlled identity key as an authorization or proof key.

### Recommendation
Reject identity public keys and identity nonce commitments before verification. At minimum, `SchnorrSignature::verify` should return `false` when `public_key` or `R` is identity, `batch_verify` should queue a guaranteed-failing statement or return an error for those cases, and aggregate verification should apply the same checks to every supplied key and `R`. Protocol-level readers should separately reject identity points wherever the point semantically represents a public key.

### Proof of Concept
```rust
use ciphersuite::{
  group::{ff::PrimeField, Group, GroupEncoding},
  Ciphersuite,
};
use schnorr::SchnorrSignature;

fn forged_identity_signature<C: Ciphersuite>() {
  // The identity is a canonical group element and is accepted by read_G.
  let identity_bytes = C::G::identity().to_bytes();
  let public_key = C::read_G(&mut identity_bytes.as_ref()).unwrap();

  // Choose s = 1 and R = sG = G.
  let mut signature_bytes = Vec::new();
  signature_bytes.extend_from_slice(C::generator().to_bytes().as_ref());
  signature_bytes.extend_from_slice(C::F::ONE.to_repr().as_ref());
  let signature = SchnorrSignature::<C>::read(&mut signature_bytes.as_slice()).unwrap();

  // Any challenge works because challenge * identity == identity.
  let challenge = C::F::ONE;

  assert!(signature.verify(public_key, challenge));
}
```

### Citations

**File:** crypto/schnorr/src/lib.rs (L50-53)
```rust
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
```

**File:** crypto/schnorr/src/lib.rs (L88-99)
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
```

**File:** crypto/schnorr/src/lib.rs (L108-110)
```rust
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
  }
```

**File:** crypto/ciphersuite/src/lib.rs (L95-99)
```rust
    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
```
