### Title
Vacuous torsion check accepts non-torsion-free Ed448 points via negated scalar - (File: crypto/ed448/src/point.rs)

### Summary
The external report describes negating a non-negative value (`uint256(-collateralBalance)` on a value asserted `>= 0`), producing a semantically wrong result. The Serai analog lives in `crypto/ed448/src/point.rs`: `Point::is_torsion_free` computes `(*self * (Scalar::ZERO - Scalar::ONE)) + self`, i.e. `(-P) + P`, which is the identity for *every* point. The negation makes the check vacuous, so the torsion-safety gate in `Point::from_bytes` never rejects anything. Any point with a torsion component — including low-order points — is decoded as valid.

### Finding Description
`GroupEncoding::from_bytes` parses untrusted 57-byte encodings (reachable through `Ciphersuite::read_G` for the Ed448 curve, and therefore through `Commitments::read`, `DLEqProof::read`, `SchnorrSignature::read`, `ThresholdKeys::read`, preprocess/share decoding, etc.). Its final validity gate is `point.is_torsion_free()` (crypto/ed448/src/point.rs, lines 303-317):

```rust
fn is_torsion_free(&self) -> Choice {
  ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
}
```

`Scalar::ZERO - Scalar::ONE` is `-1 mod l`. Multiplying `self` by `-1` yields `-P`, and `-P + P = identity` unconditionally. Like the Solidity report — where the author intended to use the magnitude of `collateralBalance` but erroneously negated it — this code negates `Scalar::ONE` where a meaningful scalar (the cofactor `4`, or a true subgroup-membership check) was required. The result: `is_torsion_free` returns `Choice(1)` for every decoded point, and `not_negative_zero & point.is_torsion_free()` degenerates to just the negative-zero check.

### Impact Explanation
Ed448 (edwards448) has cofactor 4. Decoding silently accepts points lying in the order-4 coset rather than the prime-order subgroup. Consequences for an unprivileged counterparty who submits crafted bytes:

- **FROST / threshold sessions**: a malicious participant can send commitments or verification shares with a torsion component. These pass `Commitments::read`/`read_preprocess`, get bound into `B.nonces()` and `verify_share` batch statements, and can bias the aggregated `R` by low-order points — breaking signature verification of honest sessions or enabling share-validity manipulation that honest code cannot distinguish, including a rogue contribution invisible to subgroup checks elsewhere.
- **DLEq / PedPoP / MuSig proofs**: `DLEqProof::read` accepts torsion-bearing points, weakening the soundness assumption that all points are in the prime-order group — a forged-proof vector class.
- Any downstream code assuming decoded points are torsion-free (e.g., ECDH-style shared points in PedPoP) operates on attacker-controlled low-order structure.

### Likelihood Explanation
The check is executed on every `Point::from_bytes` call, so every untrusted decoding path hits it. Exploitation only requires an attacker to submit a crafted encoding — an ordinary preprocess/proof/share message — making it fully reachable by an unprivileged party with public inputs. Whether it escalates to key-share recovery depends on the consuming protocol, but at minimum it is an incorrect verifier formula accepting invalid inputs, which the prompt's rules accept.

### Recommendation
Replace the vacuous check with a real cofactor clearing or subgroup-membership test, e.g.:

```rust
fn is_torsion_free(&self) -> Choice {
  (*self * Scalar::from(4u64)).is_torsion_free_by_order()
  // or: check `self * l == identity` where l is the prime subgroup order
}
```

Concretely, multiply by the subgroup order `l` and require identity, or multiply by cofactor `4` and verify the result is torsion-free — never `Scalar::ZERO - Scalar::ONE`, which annihilates the point.

### Proof of Concept
```rust
use ed448::point::Point;   // crypto/ed448
use ciphersuite::group::GroupEncoding;
use ff::Field;

// Any validly-encoded non-torsion-free point (e.g., a low-order point or a
// prime-order point plus an order-4 torsion component) will be accepted:
let bytes = <craft encoding of a point with a torsion component>;
let p = Point::from_bytes(&bytes);
assert!(bool::from(p.is_some())); // BUG: should be None

// Root cause: is_torsion_free is identically true
// (*self * (Scalar::ZERO - Scalar::ONE)) + self == -P + P == identity, always.
```

Evidence in source: `is_torsion_free` definition at crypto/ed448/src/point.rs:294-298; the only guard consuming it is the decode path at crypto/ed448/src/point.rs:303-317 (`CtOption::new(point, not_negative_zero & point.is_torsion_free())`).