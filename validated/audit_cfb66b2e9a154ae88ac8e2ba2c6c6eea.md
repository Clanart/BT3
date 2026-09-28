### Title
Panic on crafted Ed448 point/proof via `z = 0` projective output — (`crypto/ed448/src/point.rs`)

### Summary
`Point::to_bytes` unconditionally unwraps the inversion of the projective `z` coordinate (`let z = self.z.invert().unwrap()`). The twisted-Edwards addition formula used by `Add for Point` is not complete for edwards448 (whose curve constant `d` is a square mod `p`), so an on-curve, torsion-free attacker-supplied point can drive `z = F * G_ = 1 - d²x⁴y⁴` to `0`. Any subsequent `to_bytes()` call on the resulting point panics, crashing the verifier — the Rust analog of CVE-2017-7614's null-pointer crash on crafted input.

### Finding Description
- `crypto/ed448/src/point.rs`, `impl Add for Point` (lines 104–126) computes `z = F * G_` where `F = B - E`, `G_ = B + E`, `B = (z1·z2)²`, `E = D·x1·x2·y1·y2`. For `P + (-P)` with `z1 = z2 = 1`, this reduces to `z = 1 - d²x⁴y⁴`. Since edwards448's `d` is a square in `GF(p)`, on-curve points with `d·x²·y² = ±1` (i.e., `y = ±x` points satisfying `-x² + y² = 1 + d x²y²`) make this `z` equal `0`, producing a projectively-invalid point.
- `Point::to_bytes` (lines 325–334) then executes `self.z.invert().unwrap()`, which returns `None` for `z = 0` and panics.
- This is reachable from untrusted bytes: `Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-100`) accepts any canonical torsion-free point encoding, and `DLEqProof::read` (`crypto/dleq/src/lib.rs:191-193`) reads attacker-controlled `c` and `s` scalars. `DLEqProof::verify` → `verify_statement` (`crypto/dleq/src/lib.rs:146-157`) computes `nonce = (generator * s) - (point * c)` — exactly an `Add`/`Sub` of two points — and immediately calls `nonce.to_bytes()` inside `Self::transcript`. An attacker chooses `point = A` and `c, s` such that `s·G - c·A` is the exceptional sum producing `z = 0` (e.g., `A = (s·c⁻¹)·G` positioned so the negation step hits `d·x²·y² = ±1`), then the `to_bytes` call panics. The same pattern applies anywhere a serialized/canonicality-checked point is produced by arithmetic on attacker-influenced points (FROST `nonce.to_bytes()` in challenge computation, batch-verifier intermediate encodings, `read_G`'s re-encoding check on downstream results).

### Impact Explanation
Remote, unauthenticated denial of service: a single crafted DLEq proof / point encoding crashes any node verifying it (threshold coordinator, PedPoP participant verification, or any consumer of `crypto/dleq` + `crypto/ed448`). Availability loss for the validator/processor process; matches the "application crash via crafted input" class of the reference CVE.

### Likelihood Explanation
The panic site and the non-complete formula are deterministic code paths; reachability depends only on the existence of on-curve ed448 points with `d·x²·y² = ±1`, which follows from `d` being square mod `p` for edwards448. The attacker fully controls the point bytes (`read_G`) and scalars (`DLEqProof::read`), so constructing the exceptional sum requires only offline field arithmetic — no secret state, no collusion.

### Recommendation
- Make `Point::to_bytes` handle `z = 0` without panicking (e.g., return a fixed encoding or saturate `z` to a non-zero representative via `conditional_select` before inversion).
- Alternatively, harden `Add`/`double` to normalize exceptional outputs (detect `z = 0` and substitute a canonical projective form of the identity), or switch to a verified-complete addition formula.
- Audit all `invert().unwrap()` / `expect` sites reachable from `read_G`, `DLEqProof::read`, `SchnorrSignature::read`, and FROST nonce/commitment handling for similar crash-on-crafted-input paths.

### Proof of Concept
```rust
// Attacker perspective, targeting a verifier of DLEqProof<Ed448Point>.
// 1. Solve on-curve: find (x, y) with -x^2 + y^2 = 1 + d x^2 y^2 and d x^2 y^2 = -1
//    (equivalently y = x, -x^2 + x^2 = 0 = 1 + d x^4 -> d x^4 = -1; solvable since d is square).
// 2. Encode P = (x, y) canonically; submit bytes to read_G / inside a proof.
// 3. Submit DLEqProof { c, s } where point A satisfies s*G - c*A = P + (-P) path,
//    e.g. choose scalar k, A = k*G, c = 1, s = k, then verify computes s*G - c*A = A - A.
//    The formula yields z = 1 - d^2 x_A^4 y_A^4 = 0 for the crafted A.
// 4. verify_statement calls nonce.to_bytes() -> self.z.invert().unwrap() -> panic.

// Direct reproduction inside the crate:
// let p = /* point with d*x^2*y^2 == -1 */;
// let neg = -p;
// let bad = p + neg;          // bad.z == FieldElement::ZERO
// bad.to_bytes();             // panics: "called `Option::unwrap()` on a `None` value"
// (crypto/ed448/src/point.rs:326)
```
The crash occurs because `to_bytes` assumes `z` is always invertible, which the incomplete addition formula does not guarantee for attacker-selectable on-curve inputs.