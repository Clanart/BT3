### Title
Ed448 point decoding accepts non-prime-order points because `is_torsion_free` is tautological - ([File: crypto/ed448/src/point.rs])

### Summary
`Point::is_torsion_free` computes `(-P) + P`, which is always the identity for any group element, so its return value is always true. [1](#0-0)  Consequently, `Point::from_bytes` accepts canonical encodings of torsion points, including the order-2 point `(0, -1)`, despite attempting to reject non-torsion-free points. [2](#0-1) 

### Finding Description
`GroupEncoding::from_bytes` decodes `y`, recovers `x`, rejects negative zero, and then gates acceptance on `point.is_torsion_free()`. [2](#0-1)  The predicate is implemented as `((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()`. [3](#0-2)  Since `Scalar::ZERO - Scalar::ONE` is the scalar `-1`, this expression is always `-P + P == identity`; it never checks whether multiplying the point by the prime subgroup order produces the identity. [1](#0-0) 

The default `Ciphersuite::read_G` trusts `from_bytes` and then only verifies that the accepted point round-trips to the same canonical encoding. [4](#0-3)  Thus untrusted bytes passed through `Ed448::read_G`, `SchnorrSignature::read`, commitment decoding, or other public parsing paths can introduce points outside the intended prime-order subgroup. [5](#0-4) 

### Impact Explanation
This is an incorrect verifier/parser formula: the advertised torsion-freedom check performs no filtering. [1](#0-0)  An unprivileged party can supply a canonical non-prime-order Ed448 point through `read_G` and have it accepted by downstream signature, proof, commitment, or key-share verification logic that assumes `PrimeGroup` inputs are subgroup points. [4](#0-3) 

This expands the algebraic input space with unintended cofactor components and invalidates the security premise that decoded Ed448 points are torsion-free. [2](#0-1) 

### Likelihood Explanation
The bug is deterministic and requires no secret state or malicious validator behavior: every non-prime-order point that decodes successfully passes the tautological check. [6](#0-5)  Any externally supplied Ed448 point processed through the canonical `read_G` path is affected. [4](#0-3) 

### Recommendation
Replace `is_torsion_free` with a check that multiplies the decoded point by the Ed448 prime subgroup order and requires the result to be the identity. [1](#0-0)  Do not express that multiplication through `Scalar`, whose zero value represents integer zero rather than the subgroup order. [7](#0-6)  Add a regression test proving that the canonical encoding of `(0, -1)` is rejected by `GroupEncoding::from_bytes` and `Ed448::read_G`. [2](#0-1) 

### Proof of Concept
The order-2 point `(0, -1)` has canonical 57-byte encoding `fe ff ff ... ff 00`, where byte 0 is `0xfe`, bytes 1 through 55 are `0xff`, and byte 56 is zero for a positive `x` sign. [8](#0-7)  During decoding, `y = -1` and `x = 0`; `x.is_zero() & sign` is false because the sign bit is zero, so `not_negative_zero` is true. [9](#0-8)  Acceptance then depends on `point.is_torsion_free()`, which evaluates `(-P) + P` and necessarily returns true for this order-2 point. [6](#0-5) 

```rust
// y = -1, sign(x) = 0: the order-2 Ed448 point (0, -1).
let mut encoding = [0xff; 57];
encoding[0] = 0xfe;
encoding[56] = 0x00;

// This succeeds because is_torsion_free() is tautological.
let point = Option::<Point>::from(Point::from_bytes(&encoding));
assert!(point.is_some());
```

The expected secure behavior is for this decoding to return `None`; instead, `from_bytes` returns the point because the subgroup predicate is always true. [6](#0-5)

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

**File:** crypto/schnorr/src/lib.rs (L49-53)
```rust
impl<C: Ciphersuite> SchnorrSignature<C> {
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
```

**File:** crypto/ed448/src/scalar.rs (L22-24)
```rust
// 2**446 - 13818066809895115352007386748515426880336692474882178609894547503885
pub(crate) const MODULUS: U448 = U448::from_be_hex(MODULUS_STR);

```

**File:** crypto/ed448/src/backend.rs (L248-255)
```rust
      fn from_repr(bytes: Self::Repr) -> CtOption<Self> {
        let res = U448::from_le_slice(&bytes[.. 56]);
        CtOption::new($FieldName(Residue::new(&res)), res.ct_lt(&$MODULUS) & bytes[56].ct_eq(&0))
      }
      fn to_repr(&self) -> Self::Repr {
        let mut repr = GenericArray::<u8, U57>::default();
        repr[.. 56].copy_from_slice(&self.0.retrieve().to_le_bytes());
        repr
```
