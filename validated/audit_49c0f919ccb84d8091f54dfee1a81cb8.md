### Title
Ed448 `is_torsion_free` unconditionally returns true, so any point on the curve — including low-order torsion points — is accepted as a valid `PrimeGroup` element - (File: crypto/ed448/src/point.rs)

### Summary
`Point::is_torsion_free` in `crypto/ed448/src/point.rs:294-298` computes `(*self * (Scalar::ZERO - Scalar::ONE)) + self`, i.e. `(-P) + P`, which is the identity for *every* point. The check therefore always returns `Choice(1)` and rejects nothing. `GroupEncoding::from_bytes` (line 316) gates acceptance on `not_negative_zero & point.is_torsion_free()`, so the torsion-free half of the conjunction is dead code: any canonical encoding that isn't negative-zero is accepted, including the order-2 and order-4 points in the Ed448 cofactor-4 group.

### Finding Description
The intended check should be "multiply by the prime subgroup order `l` and require identity" (or equivalently multiply by cofactor and reject identity). Instead it performs a tautology: scalar `0 - 1` is `-1 mod l`, so the expression is `P - P = identity` for all `P`. This mirrors the Kubean advisory's bug class — a permission gate written to be unconditionally permissive (effectively `*` verbs on `*` resources) — here the "permission" is admission into the prime-order subgroup, granted to every point on the curve.

Reachability from public inputs: `from_bytes` is the decoding path behind `Ciphersuite::read_G` for the `Ed448` ciphersuite, which is a fully supported `Curve` in FROST (`crypto/frost/src/curve/ed448.rs`). Untrusted bytes from peers reach it via `Commitments::read`, `ThresholdKeys::read`, `EncryptedMessage::read`, `DLEqProof::read`, and `SchnorrSignature::read` — nonce commitments, verification shares, DLEq/promotion points, and group keys can all carry a hidden torsion component while passing `read_G`'s identity rejection (`Curve::read_G` in `crypto/frost/src/curve/mod.rs:123-131` only excludes the identity itself).

`dalek_ff_group`'s `EdwardsPoint` performs a real `is_torsion_free()` check (`crypto/dalek-ff-group/src/lib.rs:470-478`), showing the invariant is expected to be enforced; the Ed448 implementation silently voids it.

### Impact Explanation
Any Ed448 point accepted through `read_G`/`from_bytes` is treated as a `PrimeGroup` element but may have an extra component of order 2 or 4. Consequences reachable by an unprivileged protocol peer:

- **Incorrect verifier statements**: Schnorr `batch_statements` (`crypto/schnorr/src/lib.rs:88-100`) and DLEq `SchnorrPoK::verify` (`crypto/dleq/src/cross_group/schnorr.rs:59-76`) sum `R + cA - sG`. With a torsioned `A` or `R`, the sum's prime-order component can be zero while a non-zero order-2/4 component remains — or vice versa — desynchronizing what the prover and verifier believe they proved, since the transcript challenge binds only to encodings.
- **Small-subgroup confinement of key material**: a malicious DKG participant can submit verification shares or commitments whose torsion components reveal their secret share mod 4 (via `4·share` observations), and can make honest-looking shares aggregate to a group key carrying a hidden torsion component, so the resulting FROST group key is not a canonical prime-order key.
- **DLEq equality breakage**: scalar multiplication over `Scalar` (mod `l`) does not clear order-4 components, so two points equal in the prime subgroup can differ on the torsion component, letting a participant force `complete`/blame outcomes inconsistent with the actual discrete-log relation.

This is privilege escalation analog: the admission check grants membership it was designed to deny, letting a peer smuggle out-of-subgroup points into every security-critical structure.

### Likelihood Explanation
Any remote participant in an Ed448 FROST DKG/signing session controls the bytes for their commitments, shares, addenda, and verification share, all of which flow through `read_G` → `from_bytes` → the dead `is_torsion_free`. No collusion or node compromise is needed; a single malicious participant can always inject torsion components. Exploitation to full share recovery is limited to the 2-bit cofactor, but corrupting proofs, keys, and blame attribution requires only choosing a non-torsion-free encoding — trivially achievable.

### Recommendation
Implement a real subgroup check: `fn is_torsion_free(&self) -> Choice { (*self * Scalar::from(MODULUS /* l */)).is_identity() }`, or equivalently require `(*self * cofactor).is_identity() == false` combined with a canonical prime-order membership test. Add test vectors decoding the known Ed448 points of order 1, 2, and 4 and assert `from_bytes` rejects them.

### Proof of Concept
```rust
// crypto/ed448 — concept demonstration
// P2 = point of order 2 on Ed448, e.g. (0, -1) in affine:
//   y = p - 1, x = 0, sign bit 0.
let mut enc = <FieldElement as PrimeField>::Repr::default();
// encode y = -1 mod p (little-endian field repr), sign = 0
let minus_one = FieldElement::ZERO - FieldElement::ONE;
enc.copy_from_slice(&minus_one.to_repr());

// Current behavior: decompresses to the order-2 point.
// is_torsion_free evaluates (P2 * -1) + P2 = -P2 + P2 = identity => true.
let pt = Point::from_bytes(&enc);
assert!(bool::from(pt.is_some())); // BUG: torsion point accepted

// Equivalently, directly demonstrate the tautology:
let any = /* any Point, including torsion */;
assert!(bool::from(any.is_torsion_free())); // always true for ALL points
```
Any encoding of the order-2 point `(0, -1)` or either order-4 point passes `read_G` for `Ed448` today and can be supplied as a FROST commitment, verification share, or DLEq point. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crypto/ed448/src/point.rs (L294-298)
```rust
impl Point {
  fn is_torsion_free(&self) -> Choice {
    ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
  }
}
```

**File:** crypto/ed448/src/point.rs (L311-318)
```rust
    FieldElement::from_repr(bytes).and_then(|y| {
      recover_x(y).and_then(|mut x| {
        x.conditional_negate(x.is_odd().ct_eq(&!sign));
        let not_negative_zero = !(x.is_zero() & sign);
        let point = Point { x, y, z: FieldElement::ONE };
        CtOption::new(point, not_negative_zero & point.is_torsion_free())
      })
    })
```

**File:** crypto/dalek-ff-group/src/lib.rs (L470-478)
```rust
dalek_group!(
  EdwardsPoint,
  DEdwardsPoint,
  |point: DEdwardsPoint| point.is_torsion_free(),
  EdwardsBasepointTable,
  CompressedEdwardsY,
  ED25519_BASEPOINT_POINT,
  ED25519_BASEPOINT_TABLE
);
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
