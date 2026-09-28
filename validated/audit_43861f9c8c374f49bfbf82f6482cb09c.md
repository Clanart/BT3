### Title
`ThresholdKeys::read` performs attacker-controlled allocations and quadratic interpolation work before validating `t <= n` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes `t`, `n`, and `i` directly from an untrusted byte stream, then reads `n` scalars (`Interpolation::Constant`) and `n` group elements into a `HashMap`, and only afterwards calls `ThresholdParams::new`/`ThresholdKeys::new` to validate the parameters. `ThresholdKeys::new` then computes `group_key` by calling `interpolation_factor` (an O(t) Lagrange loop) once per participant in `1..=t`, yielding O(t²) field operations — all driven by two u16 fields the attacker fully controls.

### Finding Description
The bug class of CVE-2019-19048 is attacker-triggerable resource consumption on a path where failure/validation is handled late. In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632):

- `t`, `n`, `i` are read as raw `u16`s from the stream (lines 591-602).
- If the interpolation byte is `0`, `n` scalars are read via `C::read_F` (lines 607-613); `n` points are always read via `read_G` (lines 620-623). Each `read_G` performs a full point decompression plus a canonicality re-encoding check (`ciphersuite/src/lib.rs:91-100`) — the most expensive primitive in the crate, executed up to 65,535 times per message.
- Only then is `ThresholdKeys::new` invoked, which validates `t <= n` inside `ThresholdParams::new` (line 626), and computes the group key as `t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum()` (lines 376-378). `interpolation_factor` for `Interpolation::Lagrange` loops over the full `included` list (lines 234-246), so `new` is O(t²) in field multiplications and inversions — ~4.3×10⁹ field ops for `t = n = 65535` — on top of ~131k deserializations and a 65k-entry `HashMap`.

An attacker supplying a blob with `t = n = u16::MAX`, interpolation byte `0`, and `2·n` well-formed encodings forces each call to perform seconds-scale CPU work and multi-megabyte allocation before any semantic rejection. Repeating the input yields sustained CPU/memory exhaustion, the analog of the repeated `crypto_reportstat()` leak.

### Impact Explanation
Any code path that feeds untrusted bytes to `ThresholdKeys::read` (e.g., peer-supplied or externally stored key material during recover/promote flows in the `dkg`/`frost` stack) can be driven into unbounded-per-message compute and memory consumption, denying service to the victim process. No secret is leaked, but availability of the signing/key-management process is lost.

### Likelihood Explanation
Exploitation requires the attacker to reach a `ThresholdKeys::read` call with controlled bytes. The serialized format is attacker-defined up to the curve-ID check, and both `n` point deserializations and the O(t²) interpolation execute before `t`/`n` consistency is checked, so a single ~4 MB crafted blob suffices per trigger.

### Recommendation
Validate `t`, `n`, `i` via `ThresholdParams::new` immediately after reading them, before allocating or deserializing dependent on `n`. Cap `n` at a protocol-sane maximum (e.g., the actual multisig size, which is far below `u16::MAX`) and reject `Constant` interpolation unless `t == n` before reading the coefficient vector.

### Proof of Concept
Construct a byte stream for `ThresholdKeys::<Secp256k1>::read`:
- `u32` length + correct `C::ID` for Secp256k1,
- `t = 0xFFFF`, `n = 0xFFFF`, `i = 0x0001`,
- interpolation byte `1` (Lagrange) to skip the scalar reads,
- secret share: one canonical scalar encoding,
- `n` copies of a valid canonical point encoding (e.g., the generator).

Passing this to `ThresholdKeys::read` causes ~65k `read_G` decompressions and then `ThresholdKeys::new` executes `interpolation_factor` 65,535 times, each iterating 65,535 participants — ~4.3×10⁹ field multiplications plus a modular inversion per factor — consuming seconds of CPU and megabytes of memory from a ~2.2 MB input, before any parameter validation rejects the blob.