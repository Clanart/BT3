### Title

Ed448 accepts low-order public keys, enabling Schnorr signature forgery - ([File: crypto/ed448/src/point.rs](crypto/ed448/src/point.rs))

### Summary

`Ed448::read_G` accepts non-identity low-order points because `Point::is_torsion_free` is a tautological check. An attacker can encode the order-2 point `T = (0, -1)` as a public key and forge Schnorr signatures for arbitrary messages by choosing whether `c * T` equals `0` or `T`.

### Finding Description

`Point::from_bytes` is intended to reject points outside the prime-order subgroup by testing `point.is_torsion_free()` [1](#0-0) . However, `is_torsion_free` computes `(-P) + P` and checks whether the result is identity [2](#0-1) . That equation is true for every point, so it never rejects torsion.

`Ciphersuite::read_G` only requires `from_bytes` to succeed and the encoding to round-trip [3](#0-2) . Unlike FROST's stricter `Curve::read_G`, it does not even reject identity [4](#0-3) . Consequently, the canonical Ed448 encoding of the non-identity order-2 point is accepted wherever `Ciphersuite::<Ed448>::read_G` parses public keys or proof points.

Schnorr verification checks `R + cA - sG == 0` [5](#0-4) . If `A` has order 2, `cA` is either `0` or `A`, depending only on the parity of `c`. An attacker can locally search for an `s` such that either:

- `R = sG` and `c` is even, or
- `R = sG + A` and `c` is odd.

The resulting `(R, s)` satisfies verification without knowing a discrete logarithm for `A`.

### Impact Explanation

This permits forged Schnorr signatures and Schnorr-style proofs under attacker-submitted low-order Ed448 public keys. The forged object is reachable through canonical untrusted bytes passed to `read_G`, `SchnorrSignature::read`, and `verify`; no malformed or non-canonical encoding is required.

The attack cannot directly forge signatures for an honest prime-order key already fixed by the protocol. It applies when an attacker controls a public key, commitment key, or proof context accepted through `Ciphersuite::<Ed448>::read_G`.

### Likelihood Explanation

The attack is deterministic except for the challenge parity search, which has approximately a 1/2 success probability per candidate. The attacker can evaluate challenges locally because the public key, message, and candidate nonce point are public.

The order-2 point is easy to construct: on Ed448, `(x, y) = (0, -1)` is non-identity and has order 2. Its encoding is canonical and therefore passes the round-trip check.

### Recommendation

Replace `Point::is_torsion_free` with a real prime-order subgroup check based on multiplication by the Ed448 group order `q`, not by a scalar reduced modulo `q`. Since `q` is zero modulo the scalar field, this requires an integer/big-scalar multiplication path or another explicit cofactor-aware subgroup membership test.

At a minimum, explicitly reject the known low-order encodings, but a complete subgroup-membership check is preferable.

### Proof of Concept

Let:

- `G` be the Ed448 generator.
- `T` be the order-2 point `(0, -1)`.
- `A = T` be the attacker-controlled public key.
- `c = H(R, A, m)` be the Schnorr challenge.

For candidate scalar `s`:

1. Compute `R0 = sG` and `c0 = H(R0, A, m)`.
   - If `c0` is even, return signature `(R0, s)`, since `R0 + c0T = sG`.
2. Otherwise compute `R1 = R0 + T` and `c1 = H(R1, A, m)`.
   - If `c1` is odd, return signature `(R1, s)`, since `R1 + c1T = sG + T + T = sG`.
3. Repeat with another `s` until either candidate verifies.

The vulnerable check that admits `T` is:

```rust
// crypto/ed448/src/point.rs
fn is_torsion_free(&self) -> Choice {
  ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
}
```

This computes `-P + P == identity`, which is true for every point and does not establish prime-order membership.

### Citations

**File:** crypto/ed448/src/point.rs (L294-297)
```rust
impl Point {
  fn is_torsion_free(&self) -> Choice {
    ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
  }
```

**File:** crypto/ed448/src/point.rs (L303-317)
```rust
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
