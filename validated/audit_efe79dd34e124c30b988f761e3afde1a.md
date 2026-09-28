### Title
Pre-validation attacker-controlled length causes speculative heap allocation in `ThresholdKeys::read` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes an attacker-controlled `u16` participant count `n` and immediately performs `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` coefficient vector — before `n` is validated against `t`, `i`, or the actual remaining length of the input stream. This is the same bug class as the ActiveMQ WireFormatInfo issue: an unauthenticated size field is trusted to size an allocation during pre-validation parsing.

### Finding Description
In `crypto/dkg/src/lib.rs` (`ThresholdKeys::read`, lines 574-632), the deserialization order is:

1. Read `C::ID` (checked against expected curve ID — bound to a constant, safe).
2. Read `t`, `n`, `i` as raw `u16`s directly from the reader (lines 591-602). `n` is not validated at this point.
3. Read the interpolation tag. If it is `0` (Constant), the code executes `Vec::with_capacity(usize::from(n))` and then pushes `n` scalars read via `C::read_F` (lines 606-613).
4. Only afterwards, at line 626, is `ThresholdParams::new(t, n, i)` invoked — the first point where `n` is semantically validated (`n != 0`, `t <= n`, `i <= n`).

The `with_capacity` call at line 608 allocates `n * size_of::<C::F>()` bytes (e.g., `65535 * 32 ≈ 2 MiB` for a 256-bit field) purely from a 2-byte attacker-controlled field, before any bounds check and before the stream's actual length is known. A sender can provide ~10 bytes total (valid curve ID length + ID + `t`, `n = 0xFFFF`, `i` + interpolation tag `0`) and force the 2 MiB allocation; the subsequent `C::read_F` fails almost immediately on the exhausted reader, but the allocation has already been performed. Repeating this across many `ThresholdKeys::read` calls creates allocator churn and transient memory pressure disproportionate to the bytes transmitted (~300,000x amplification per message, bounded by the `u16` width of `n`).

The same pattern exists at lines 620-623 for `verification_shares` (`n` `read_G` calls into a `HashMap`), though there the allocation grows incrementally and is proportional to bytes actually present, so it is not exploitable the same way.

Note the related `SchnorrAggregate::read` in `crypto/schnorr/src/aggregate.rs` (lines 77-88) reads a `u32` count but uses `vec![]` + `push`, so allocation is proportional to actual input — it does not exhibit the pre-allocation flaw.

### Impact Explanation
An unprivileged party able to feed serialized bytes to `ThresholdKeys::read` (listed as an in-scope untrusted-input surface) can force repeated speculative multi-megabyte heap allocations with a handful of bytes per message, degrading or crashing the host under sustained input. Impact is capped by `n` being a `u16` (~2 MiB per call for typical curves) and by the allocation being freed when parsing fails, so this is a moderate DoS rather than an unbounded OOM.

### Likelihood Explanation
Exploitation requires an attacker-reachable path that invokes `ThresholdKeys::read` on attacker-controlled bytes. The allocation itself is trivially triggerable (7 bytes of header plus a `0` interpolation tag). The amplification is bounded and transient — no persistent memory growth per message unless the reader is slow or calls are parallelized — which lowers severity relative to the ActiveMQ bug (which used an unbounded integer). Medium likelihood, Medium impact.

### Recommendation
Validate `n` before allocating: construct `ThresholdParams::new(t, n, i)` immediately after reading the three `u16`s, and reject `n` values that are inconsistent before performing `Vec::with_capacity`. Additionally, avoid `with_capacity(n)` entirely and let the `Vec` grow as `read_F` succeeds, or cap `n` against the remaining stream length when it is knowable.

### Proof of Concept
Conceptual bytes fed to `ThresholdKeys::<C>::read`:

```text
[id_len: u32 LE][C::ID][t = 0x0001][n = 0xFFFF][i = 0x0001][interp = 0x00]
```

With a valid `C::ID` and `n = 65535`, line 608 executes `Vec::with_capacity(65535)` (~2 MiB heap reservation) before `ThresholdParams::new` validation at line 626 and before any check that the stream actually contains 65535 scalars. A short input still triggers the allocation; the subsequent `C::read_F` errors out only after the memory is reserved. Repeating in a loop amplifies allocator pressure.