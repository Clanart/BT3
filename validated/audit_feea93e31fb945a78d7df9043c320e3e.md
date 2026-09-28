### Title
Broken torsion check in Ed448 `Point::from_bytes` accepts small-order/torsion points, enabling Schnorr signature forgery - (File: crypto/ed448/src/point.rs)

### Summary
`Point::is_torsion_free` in `crypto/ed448/src/point.rs` is mathematically vacuous: it computes `(*self * (Scalar::ZERO - Scalar::ONE)) + self`, i.e. `(-P) + P`, which is the identity for every point. As a result, the `GroupEncoding::from_bytes` implementation accepts points with a non-trivial 4-torsion component (Ed448 has cofactor 4). An unprivileged party can feed such encodings through `C::read_G` (which only checks canonicality and, for `Curve`, non-identity) into `SchnorrSignature::read`/`verify`, `Commitments::read`, `DLEqProof`-adjacent flows, or `ThresholdKeys::read`, and forge signatures against a torsion public key.

### Finding Description
In `crypto/ed448/src/point.rs`, the torsion check is:

```rust
impl Point {
  fn is_torsion_free(&self) -> Choice {
    ((*self * (Scalar::ZERO - Scalar::ONE)) + self).is_identity()
  }
}
```

`Scalar::ZERO - Scalar::ONE` is `-1`, so the expression is `-P + P = identity` unconditionally — `is_torsion_free` always returns `Choice::from(1)` for any parsed point, including the 3 non-identity points of order 2/4.

This result gates acceptance in `GroupEncoding::from_bytes`:

```rust
CtOption::new(point, not_negative_zero & point.is_torsion_free())
```

The function already correctly rejects `x = 0` with the sign bit set ("negative zero"), and requires canonical `y` via `FieldElement::from_repr`, but the only subgroup-membership check is the broken one above. Everything downstream inherits this:

- `Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs`) decodes via `from_bytes` and only enforces canonical re-encoding — torsion points pass.
- `Curve::read_G` (`crypto/frost/src/curve/mod.rs`) adds only an identity rejection — order-2/order-4 points pass.
- `SchnorrSignature::verify` checks `R + c·A - s·G = identity` via `multiexp_vartime` with no cofactor clearing (`crypto/schnorr/src/lib.rs`, `batch_statements`).

### Impact Explanation
Forgery of Schnorr signatures. The verifier equation is `R + c·A - s·G = 0`. Let `T` be a point of order 4 (accepted as a public key). The attacker wants `R = s·G - c·T` where `c = HRAM(R, A, msg)`. Since `c·T` depends only on `c mod 4`, the attacker can pick `s` arbitrarily and iterate over the four guesses `g ∈ {0,1,2,3}`:

1. Set `R_g = s·G - g·T` (both components serialize canonically; `R_g` is non-identity).
2. Compute `c_g = HRAM(R_g, T, msg)`.
3. If `c_g ≡ g (mod 4)`, then `(R_g, s)` satisfies the verification equation: `R_g + c_g·T - s·G = s·G - g·T + c_g·T - s·G = (c_g - g)·T = 0`.

Each guess succeeds with probability ~1/4, so a forged `(R, s)` for an attacker-controlled torsion public key `A = T` over any message is found in ~4 hash evaluations — no private key required. The same torsion acceptance applies to any attacker-supplied point: verification shares and nonce commitments in FROST (`Commitments::read`), generator-promotion `share`/`proof` points, and PedPoP commitments, enabling small-subgroup confinement of nonce commitments (`R = D + ρ·E` keeps its torsion component since `ρ·(E_torsion)` stays in `T_4`).

This matches the analog scope: attacker-controlled bytes reach `read_G`/`verify` with public inputs, yielding a forged signature — a concrete cryptographic break, not a misuse or integrator error.

### Likelihood Explanation
Any code path that verifies an Ed448 Schnorr signature or aggregates/batch-verifies statements over `Point` values derived from untrusted bytes is exploitable deterministically and cheaply (~4 iterations of the Fiat-Shamir hash). No collaboration, key leakage, or malicious-validator assumption is needed — only the ability to submit a public key and signature encoding, which is the documented reachable surface for `SchnorrSignature::read`/`verify`. For FROST flows, a participant can additionally place torsion components into preprocess commitments, which survive `read_G` and pollute the bound nonce sum `D + ρ·E` with a hidden small-order term.

### Recommendation
Replace the no-op check with a real subgroup-membership test. The minimal fix is to multiply the candidate point by the prime group order (i.e., the scalar field modulus `l`) and require the result to be identity:

```rust
fn is_torsion_free(&self) -> Choice {
  (*self * Scalar::from_repr(<l>::REPR).unwrap()).is_identity()
}
```

or equivalently verify `self` lies in the prime-order subgroup by checking `l * P == identity`. Alternatively, perform the decode-time check as cofactor-safeness plus forbid the identity where required by callers. The check must be performed inside `GroupEncoding::from_bytes` (as it is today) so all `read_G` consumers inherit it; `from_bytes_unchecked` should not bypass it. Add test vectors covering all four coset representatives (points of order 1, 2, and 4, and pure-torsion encodings) asserting rejection.

### Proof of Concept
1. Construct `T`, an Ed448 point of order 4 (a valid non-identity torsion point whose `y`/`sign` encoding is canonical).
2. Call `<Ed448 as Ciphersuite>::read_G(&mut enc(T))` — returns `Ok(T)` today, demonstrating the broken check. `Point::is_torsion_free` computes `(-T) + T = identity`, so `CtOption::new(point, not_negative_zero & true)` accepts.
3. Forgery: fix public key `A = T`, message `m`, arbitrary `s`. For `g in 0..4`:
   - `R = s·G - g·T`; `c = HRAM(R, A, m)`;
   - if `c mod 4 == g`, `SchnorrSignature { R, s }.verify(A, c)` returns `true` — a valid signature for `m` under a key whose discrete log nobody knows.