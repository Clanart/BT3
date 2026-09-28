### Title
Quadratic Lagrange interpolation in `ThresholdKeys::new` reachable via untrusted `ThresholdKeys::read` bytes - (File: crypto/dkg/src/lib.rs)

### Summary

The advisory's bug class is a parse-time quadratic cost driven entirely by attacker-supplied input size. Serai's analog is `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`): it reconstructs the group key by calling `Interpolation::interpolation_factor` once per participant in `1 ..= t` (line 376-378), and each call to `interpolation_factor` for `Interpolation::Lagrange` loops over all `t` included participants, performing `t` field multiplications and a field inversion per call (`crypto/dkg/src/lib.rs:226-249`). Total cost is Θ(t²) field multiplications plus Θ(t) inversions, while the serialized input needed to trigger it is only Θ(n) bytes.

### Finding Description

`ThresholdKeys::write` serializes `t`, `n`, `i`, the interpolation variant, the secret share, and `n` verification shares (`crypto/dkg/src/lib.rs:538-562`). The corresponding `ThresholdKeys::read` (the read-side counterpart accepted as an untrusted-bytes target) deserializes `t` and `n` as attacker-controlled `u16`s and then calls `ThresholdKeys::new`, which performs the interpolation:

```rust
// crypto/dkg/src/lib.rs:376-378
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

`interpolation_factor` for Lagrange (`crypto/dkg/src/lib.rs:229-247`) iterates the full `included` list per invocation, so group-key reconstruction costs `t(t-1)` scalar muls and `t` inversions. `ThresholdParams::new` (`crypto/dkg/src/lib.rs:166-179`) only enforces `0 < t <= n <= 65535`, so a crafted blob with `t = n = 65535` is accepted, forcing ~4.3×10⁹ scalar multiplications — minutes of CPU — from a serialized key of roughly `n × point_size` (~2 MB for a 32-byte-point curve). The input/output cost ratio matches the advisory's quadratic profile (small linear input, quadratic work). The same O(k²) interpolation recurs in `ThresholdKeys::view` (`crypto/dkg/src/lib.rs:500-507`), where every included signer's verification share is multiplied by a factor that itself costs O(k).

### Impact Explanation

Any Serai component that calls `ThresholdKeys::read` on bytes influenced by an unprivileged party (deserialized key material supplied in messages, backups, or recovery flows, and any downstream `view()` / `complete()` path over an attacker-influenced `included` set) can be driven into quadratic CPU burn, stalling the processor worker — an availability-only impact, mirroring the advisory.

### Likelihood Explanation

Exploitation requires the attacker to reach a `ThresholdKeys::read`/`view` call with a large crafted `t`/`n`. `n` is normally bounded by the real validator-set size in honest deployments, which caps practical impact where keys come from trusted storage; where serialized keys are accepted from untrusted channels the attack is a straightforward byte-string with `t = n = 65535`. Severity: Medium (availability only, requires a deserialization sink reachable by untrusted input).

### Recommendation

Replace the per-participant `interpolation_factor` calls with a batched computation: compute the full denominator product once, then derive each participant's factor in O(1) amortized work (prefix/suffix products or a single product + per-element inversion via one batched inversion), reducing `ThresholdKeys::new` and `view()` from O(t²) to O(t). Additionally, cap `t`/`n` at a deployment sanity bound inside `ThresholdParams::new` or at `read` time before any interpolation runs.

### Proof of Concept

Construct a serialized `ThresholdKeys` with `t = n = 65535`, `i = 1`, `Interpolation::Lagrange`, an arbitrary scalar secret share, and `n` repetitions of the same valid encoded group element (~2 MB total). Feed it to `ThresholdKeys::read`. Deserialization succeeds on field/size grounds, then `ThresholdKeys::new` executes the line-376-378 interpolation: 65,535 iterations of `interpolation_factor`, each scanning a 65,535-element `included` list — ~4.3×10⁹ field multiplications and 65,535 inversions — versus ~2 MB of input. Equivalent-budget input with `t = n = 256` completes in ~65k muls, demonstrating the quadratic gap.

Caveat: I could not directly confirm the `ThresholdKeys::read` body in the indexed excerpts (only `write` at `crypto/dkg/src/lib.rs:538-562` was visible); the finding assumes the symmetric reader that the task rules enumerate, which parses the same fields and invokes `ThresholdKeys::new`.