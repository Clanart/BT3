### Title
Identity public keys permit universal Schnorr signature forgery - (File: crypto/schnorr/src/lib.rs)

### Summary
`SchnorrSignature::verify` accepts signatures for the identity public key without explicitly rejecting it. A signature with `R = identity` and `s = 0` satisfies the verification equation for every challenge, allowing an attacker to forge a valid signature whenever untrusted public-key bytes can select the identity point.

### Finding Description
`Ciphersuite::read_G` validates only that a point decodes and has a canonical encoding; it does not reject the identity element. [1](#0-0)  `SchnorrSignature::read` uses that permissive decoder for `R`, while callers commonly use it for public keys as well. [2](#0-1)  Verification checks `R + cA - sG == identity`, so when `A`, `R`, and `s` are identity/zero, every term vanishes and verification succeeds for any `c`. [3](#0-2) 

### Impact Explanation
An attacker can produce a signature that verifies under an attacker-supplied identity public key without knowing a discrete logarithm or private key. Because Schnorr verification is used directly by in-scope protocols such as PedPoP proofs of knowledge and aggregate signatures, any path that accepts a public key from protocol input can be made to accept a forged proof or signature.

### Likelihood Explanation
The forged signature is deterministic and does not require breaking a hash, obtaining a private key, controlling multiple parties, or exploiting malformed non-canonical encodings. Exploitability depends on an application accepting an attacker-controlled identity public key, which the decoding layer currently permits.

### Recommendation
Reject identity points in `SchnorrSignature::read`, `SchnorrSignature::verify`, `SchnorrAggregate::verify`, and every public-key decoding path intended for authentication. At minimum, add an explicit identity check to `SchnorrSignature::verify`, so direct construction cannot bypass deserialization validation.

### Proof of Concept
For a ciphersuite whose canonical identity encoding is all-zero bytes, such as `Ristretto`, the forged public key is `[0; 32]` and the forged signature is `[0; 64]`:

```rust
use ciphersuite::Ciphersuite;
use dalek_ff_group::Ristretto;
use schnorr::SchnorrSignature;

let public_key = Ristretto::read_G(&mut &[0u8; 32][..]).unwrap();
let signature =
  SchnorrSignature::<Ristretto>::read(&mut &[0u8; 64][..]).unwrap();

// Any correctly bound challenge value works.
assert!(signature.verify(public_key, arbitrary_challenge));
```

The first 32 signature bytes decode as `R = identity`, the remaining bytes decode as `s = 0`, and verification reduces to `identity + c * identity - 0G == identity`. [4](#0-3)

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
