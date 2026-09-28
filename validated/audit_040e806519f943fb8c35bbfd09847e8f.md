### Title
Ed448 point deserialization accepts torsion points, enabling Schnorr signature forgery - (crypto/ed448/src/point.rs)

### Summary
`Point::from_bytes` accepts non-prime-order Ed448 points because `is_torsion_free` computes `P * -1 + P`, which is always identity rather than a subgroup-membership check. [1](#0-0)  Attackers can register an order-four public key and forge Schnorr signatures for chosen messages whenever the Fiat–Shamir challenge is divisible by four. [2](#0-1) 

### Finding Description
`Point::is_torsion_free` uses `Scalar::ZERO - Scalar::ONE`, which is simply `-1` modulo the scalar-field order, so the expression `-P + P` is identity for every curve point. [3](#0-2)  `GroupEncoding::from_bytes` relies on that predicate before returning the decoded point, meaning torsion components are accepted despite `Point` being used as a prime-order group element. [4](#0-3)  The generic `Ciphersuite::read_G` path accepts any point returned by `from_bytes` and additionally requires only that its canonical re-encoding matches the supplied bytes. [5](#0-4) 

### Impact Explanation
Schnorr verification checks `R + cA - sG == 0`. [6](#0-5)  If `A` is an order-four torsion point, `cA` is identity whenever `c ≡ 0 mod 4`; selecting `R = sG` therefore makes the equation hold without knowledge of a discrete logarithm for `A`. [7](#0-6)  This yields forged signatures under a public key that was accepted from untrusted serialized bytes, satisfying the unintended-signature/forgery impact class.

### Likelihood Explanation
An unprivileged party can submit the 57-byte encoding of an order-four Ed448 point, such as `(x, y) = (1, 0)`, as a public key. [4](#0-3)  The order-two encoding `(0, -1)` and either sign choice for `y = 0` are canonical encodings that survive the supplied-byte equality check. [5](#0-4)  Once such a key is registered, the attacker only needs to retry candidate `R = sG` values until the public challenge satisfies `c ≡ 0 mod 4`, a one-in-four condition for a uniform challenge.

### Recommendation
Replace `is_torsion_free` with a real prime-order-subgroup membership test rather than a scalar multiplication by `-1`. [3](#0-2)  For Ed448, explicitly multiply by the prime subgroup order using an operation that can represent that order, or reject all non-subgroup points during decoding; additionally reject the identity where the protocol requires non-identity public keys. [8](#0-7)  Add tests covering order-two and order-four encodings, including `y = -1` and `y = 0` with both sign bits. [4](#0-3) 

### Proof of Concept
Use the canonical order-four encoding `A_bytes = [0u8; 56] || [0x80]`, representing `y = 0` with an odd `x`, and deserialize it through `Ed448::read_G`. [4](#0-3)  For an arbitrary message `m`, repeatedly choose scalar `s`, set `R = sG`, compute the protocol challenge `c = H(R, A, m)`, and retain a candidate where `c mod 4 == 0`. [9](#0-8)  The resulting `(R, s)` verifies because `R - sG = 0` and `cA = 0` for the order-four public key `A`. [2](#0-1)

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

**File:** crypto/schnorr/src/lib.rs (L68-83)
```rust
  /// Sign a Schnorr signature with the given nonce for the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[allow(clippy::needless_pass_by_value)] // Prevents further-use of this single-use value
  pub fn sign(
    private_key: &Zeroizing<C::F>,
    nonce: Zeroizing<C::F>,
    challenge: C::F,
  ) -> SchnorrSignature<C> {
    SchnorrSignature {
      // Uses deref instead of * as * returns C::F yet deref returns &C::F, preventing a copy
      R: C::generator() * nonce.deref(),
      s: (challenge * private_key.deref()) + nonce.deref(),
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
