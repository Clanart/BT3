### Title
Quadratic Lagrange interpolation in `ThresholdKeys::new`/`view` enables deserialization-driven denial of service - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to CVE-2026-32281 (unexpectedly expensive validation driven by attacker-controlled structure size), Serai recomputes each signer's Lagrange interpolation factor with an O(n) loop and a field inversion, and then does this once per participant — producing O(n²) field work plus n inversions when `n`/`t` approach `u16::MAX`. This is reachable with untrusted bytes through `ThresholdKeys::read`, which is explicitly on the reachable-input list.

### Finding Description
`Interpolation::interpolation_factor` iterates over every entry of `included` and performs ~2 field multiplications plus one `invert()` per call (`crypto/dkg/src/lib.rs` L226–L249). `ThresholdKeys::new` calls it `t` times to derive `group_key` (L376–L378), and `ThresholdKeys::view` calls it once per included signer (L500–L507). `ThresholdKeys::read` accepts `t`/`n` directly from the byte stream up to `u16::MAX` (L591–L631), so a small serialized payload (65535 point encodings, ~2 MB on a 32-byte-point curve) triggers:

- `n` point deserializations,
- ~`t²` ≈ 4.3×10⁹ field multiplications,
- ~`t` ≈ 65535 field inversions,

in the `ThresholdKeys::new` group-key computation alone. Any subsequent `view()` over a maximal `included` set repeats the quadratic factor computation (O(n²) muls + n inversions, plus n scalar·point multiplications). There is no caching of Lagrange coefficients, no batch inversion (e.g., Montgomery trick), and no denominator-product precomputation.

### Impact Explanation
An unprivileged party who can cause a Serai node/integrator to deserialize attacker-supplied `ThresholdKeys` (or any other bytes funneled into `ThresholdKeys::read`) can force billions of field operations from a ~2 MB input, blocking the verifying thread for an unbounded amount of time. This is a resource-amplification DoS in verification-time logic — the same bug class as the Go x509 policy-mapping issue: correct-but-quadratic validation over attacker-controlled counts.

### Likelihood Explanation
Requires the application to call `ThresholdKeys::read` (or `FrostKeys::read`, which wraps it) on data influenced by an attacker and permits `t = n` near `u16::MAX`. Work scales as n², so moderate n already yields outsized cost; the cost is fully deterministic and repeatable.

### Recommendation
Cap `t`/`n` to the deployment's real multisig size before doing any interpolation work; compute Lagrange denominators with a single batched inversion (prefix products + one inversion) reducing factor computation to O(n) inversions→1; cache per-signing-set interpolation factors in `ThresholdView`; and/or precompute `interpolation_factor` products incrementally during `view()`.

### Proof of Concept
```rust
use std::io;
use serai_dkg::{ThresholdKeys, Participant}; // crypto/dkg
use ciphersuite::Ciphersuite; // e.g. Ciphersuite for Ristretto/kp256

// Build a serialized ThresholdKeys blob with t = n = u16::MAX, Lagrange interpolation.
// (u32 ID len | ID | t | n | i | interp=1 | secret F | n * G)
// Feeding this to ThresholdKeys::read triggers:
//   - group_key computation: t calls to interpolation_factor, each O(t) field muls + 1 invert
//   => ~4.3e9 field multiplications and 65535 inversions from a ~2MB input.
ThresholdKeys::<C>::read(&mut &blob[..])?; // quadratic, no size cap enforced before work
```

Root cause: `crypto/dkg/src/lib.rs` L226–L249 (per-call O(n) factor with inversion), invoked `t` times at L376–L378 and `|included|` times at L500–L507, with attacker-controlled `t`/`n` accepted at L591–L631.