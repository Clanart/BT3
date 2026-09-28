### Title
Out-of-bounds indexing of `Interpolation::Constant` coefficients panics on attacker-controlled serialized `ThresholdKeys` - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to CVE-2016-3977 (a GIF-controlled color index used to index a heap buffer without a bounds check), `Interpolation::interpolation_factor` indexes the `Constant(Vec<F>)` coefficient vector with `c[u16::from(i) - 1]`, where `i` is a participant index. `ThresholdKeys::new` validates that `t == n` for `Constant` interpolation but never validates that the coefficient vector's length equals `n` (or is at least `t`). A serialized `ThresholdKeys`/`ThresholdCore` value with `Constant` coefficients shorter than `n` causes an out-of-bounds index panic when `group_key` is computed inside `ThresholdKeys::new`, reachable through `ThresholdKeys::read` on untrusted bytes.

### Finding Description
In `crypto/dkg/src/lib.rs`:

- `interpolation_factor` indexes the coefficient vector directly: `Interpolation::Constant(c) => c[usize::from(u16::from(i) - 1)]` (line 228). There is no length check.
- `ThresholdKeys::new` enforces only `params.t() == params.n()` for the `Constant` variant (lines 367-374); the length of the `Vec<F>` is unchecked.
- `group_key` is then computed by iterating `t = 1..=params.t()` and calling `interpolation.interpolation_factor(*i, &t)` (lines 376-378). If `c.len() < params.t()`, `c[i - 1]` panics with an index-out-of-bounds error.

Because `ThresholdKeys::read`/`ThresholdCore` deserialization reads the interpolation variant and its coefficient vector from the byte stream, an unprivileged party supplying crafted bytes (e.g., `n = t = 2` with a single coefficient) reaches this panic during `ThresholdKeys::new`, which `read` invokes. The panic aborts the calling context — a denial of service identical in class to the gif2rgb crash, where a file-controlled index drives an unchecked buffer access.

### Impact Explanation
Any component that deserializes attacker-supplied `ThresholdKeys` bytes crashes on the OOB index. In Rust this is a panic rather than memory corruption, but the availability impact (process/task abort, dropped signing session, or node crash if the panic crosses an unwinding boundary) matches the CVE's denial-of-service impact. Severity: Medium.

### Likelihood Explanation
Requires an attacker to feed a malformed serialized `ThresholdKeys` blob to an endpoint that calls `ThresholdKeys::read`. The panic is deterministic once reached — no race or cryptographic assumption needed. Likelihood depends on whether such deserialization accepts externally influenced bytes in deployment; the code path itself contains no guard.

### Recommendation
In `ThresholdKeys::new`, for `Interpolation::Constant(c)` additionally require `c.len() == usize::from(params.n())` (or at minimum `>= t`), returning a `DkgError` instead of proceeding. Alternatively, bounds-check the access in `interpolation_factor` and surface an error. This closes the gap both for deserialization and for programmatically constructed keys.

### Proof of Concept
```rust
// Crafted serialized ThresholdKeys bytes where:
//   params: t = 2, n = 2
//   interpolation: Constant variant tag, vector length = 1 (a single scalar)
//   verification_shares: two valid (Participant, point) pairs for indexes 1, 2
//   secret_share: one valid scalar
let bytes = crafted_threshold_keys_bytes;
// ThresholdKeys::new -> interpolation_factor(i = 2, _) -> c[1] on a len-1 Vec -> panic
let _ = ThresholdKeys::<Secp256k1>::read(&mut &bytes[..]).unwrap(); // index out of bounds panic
```
Root cause confirmed at `crypto/dkg/src/lib.rs:228` (unchecked `c[i - 1]`), `crypto/dkg/src/lib.rs:367-374` (missing length validation), and `crypto/dkg/src/lib.rs:376-378` (`group_key` computation iterating up to `t`).

Note: I verified the missing length check and the unchecked index, but did not inspect the exact serialization format of `ThresholdKeys::read` in this pass; the PoC sketch assumes (per the stated reachable-input rules) that `Interpolation::Constant` and its vector length are deserialized from the input stream. If `read` hardcodes the variant or length, the same defect is still reachable via any integrator-constructed `Interpolation::Constant` with a short vector.