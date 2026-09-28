### Title
Ed448 deserialization accepts torsion points, enabling forged Schnorr proofs/signatures - (crypto/ed448/src/point.rs)

### Summary
The Ed448 point decoder is intended to reject non-prime-order points, but `Point::is_torsion_free` computes `(-P) + P`, which is always the identity for every decoded point. As a result, canonical encodings of low-order Ed448 points pass `GroupEncoding::from_bytes`, pass `Ciphersuite::read_G` canonicalization checks, and can be supplied as public keys, encryption keys, commitments, or signature nonces through the various `read` APIs.

### Finding Description
`Point::is_torsion_free` is implemented as `((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()`. Since `Scalar::ZERO - Scalar::ONE` is `-1`, this evaluates `P - P == identity`, which is true unconditionally and does not test subgroup membership. [1](#0-0) 

`Point::from_bytes` relies on that predicate as its final validation before returning the decoded point. [2](#0-1)  The generic `Ciphersuite::read_G` only checks that decoding succeeds and that `to_bytes` round-trips the supplied encoding; it does not perform an additional subgroup or non-identity check. [3](#0-2) 

One reachable untrusted-byte path is `EncryptedMessage::read`, which reads `key` with `C::read_G`, reads a Schnorr proof of possession, and later verifies it with `msg.pop.verify(msg.key, ...)`. [4](#0-3) [5](#0-4) 

The same issue affects Schnorr verification generally because `SchnorrSignature::read` accepts the decoded `R` and `s`, while `verify` only checks the equation `R + cA - sG == identity`. [6](#0-5) [7](#0-6) 

### Impact Explanation
For an Ed448 public key `A = T`, where `T` has order 4, an attacker can forge a Schnorr signature by choosing `s = 0` and `R = -aT` for `a ∈ {0,1,2,3}`. Verification then succeeds whenever the challenge scalar satisfies `c ≡ a mod 4`, because the verifier computes `R + cA = -aT + cT = identity`. The attacker can vary the message or the selected `a` value until the hash-derived challenge has the required residue; only about four attempts are expected.

This bypasses the Ed448 subgroup boundary that the code explicitly attempts to enforce. In the PedPoP path, a forged proof of possession for a torsion `msg.key` would be accepted by `Decryption::decrypt_with_proof` before the DLEq proof is evaluated. More broadly, any verifier that accepts an attacker-chosen Ed448 public key through `read_G` can accept forged Schnorr signatures for that key.

### Likelihood Explanation
The attacker only needs to submit canonical serialized bytes to an exposed `read_G`-based parser and induce verification of a signature/proof under a torsion key. No secret key, validator privilege, malformed non-canonical encoding, unsafe code, or cross-group behavior is required. The limitation is that an impact requires a protocol path where the attacker controls or introduces the Ed448 public key being verified; it does not let the attacker forge signatures under an existing honest prime-order key.

### Recommendation
Replace `Point::is_torsion_free` with a real prime-order subgroup membership check for Ed448. The current expression cannot work because it always computes `P - P`. The check must determine whether the decoded point lies in the order-`l` subgroup, using a dedicated multiplication by the subgroup order or another mathematically correct Ed448 subgroup test rather than the scalar `-1`. Additionally, reject the identity point at APIs where the protocol requires a non-identity public key, nonce, commitment, or encryption key. Add tests for every low-order Ed448 point and representative mixed-order points, asserting failure through `Point::from_bytes`, `Ed448::read_G`, `SchnorrSignature::read`/`verify`, `EncryptedMessage::read`, and `Commitments::read`.

### Proof of Concept
Conceptual PoC:

```rust
// A is a canonical Ed448 point of order 4 accepted by Point::from_bytes.
let A: Point = order_four_point();

// Try the four possible discrete logarithms against A.
for a in 0u8..4 {
    let R = -(A * Scalar::from(a));
    let s = Scalar::ZERO;
    let sig = SchnorrSignature::<Ed448> { R, s };

    // Challenge must bind A, R, and the message as required by the caller.
    let c = challenge_bounding(A, R, message);

    // Since A has order 4, c*A only depends on c mod 4.
    // When c mod 4 == a, this verifies despite no prime-order secret key existing.
    if sig.verify(A, c) {
        // forged signature accepted
    }
}
```

The decisive root cause is that `A` is accepted at all: the decoded point’s `is_torsion_free` result is always true because it computes `P - P`, and `read_G` then accepts the canonical torsion encoding. [8](#0-7)

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L171-176)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L373-379)
```rust
  ) -> Result<Zeroizing<E>, DecryptionError> {
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }
```

**File:** crypto/schnorr/src/lib.rs (L51-53)
```rust
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
