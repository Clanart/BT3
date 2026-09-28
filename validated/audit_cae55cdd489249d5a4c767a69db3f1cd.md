### Title
`ThresholdKeys::read` performs attacker-controlled-length scalar/point reads before validating `ThresholdParams` - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::<C>::read` trusts the serialized `n` field (a `u16` up to 65535) and executes two length-derived loops — `n` `C::read_F` calls for `Interpolation::Constant` and `n` `C::read_G` calls for `verification_shares` — plus a `Vec::with_capacity(n)` and `HashMap` with `n` insertions, all *before* `ThresholdParams::new(t, n, i)` validates `t`/`n`/`i` at line 626 and before `ThresholdKeys::new` checks `verification_shares.len() == params.n()`. This is the same class as the external report: an externally supplied quantity drives a loop that performs expensive work (point decompression) with no sanity bound enforced up front.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` reads `t`, `n`, `i` as raw `u16`s (lines 591–602). It then:

- Reads `interpolation` byte; if `0`, builds `Interpolation::Constant` by looping `0 .. n` calling `C::read_F(reader)` with `Vec::with_capacity(n)` (lines 604–613).
- Reads `n` group elements via `C::read_G` into a `HashMap` keyed `Participant(1..=n)` (lines 620–623).
- Only afterwards calls `ThresholdParams::new(t, n, i)` (line 626), which is where any bound on `n` (the codebase bounds participant sets elsewhere, e.g., ~150 in the coordinator) would reject the value.

`read_G` performs full point decoding/validation per element, so a crafted buffer claiming `n = 65535` forces up to 65535 scalar reads and 65535 curve-point decompressions — plus the `HashMap`/`Vec` allocations — before the params check ever runs. `ThresholdKeys::read` is an explicitly untrusted-bytes entry point (deserialization of received keys/material), so the byte stream and hence `n` are attacker-controlled.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (e.g., a peer supplying key material or any message embedding a serialized `ThresholdKeys`) can make the victim perform tens of thousands of field-element parses and elliptic-curve point decompressions per message, plus proportional heap allocation, before the input is rejected. Repeating this across messages yields a CPU/memory exhaustion DoS against the processor — the analog of exhausting block gas in the original report. Magnitude is bounded by how many bytes the attacker can deliver (each iteration needs its encoded element present), so work is linear in attacker-supplied input size with a large constant factor, not truly unbounded.

### Likelihood Explanation
Reachability is real wherever serialized `ThresholdKeys` cross a trust boundary — `read` exists precisely for deserialization of received key material, and the codebase explicitly lists `ThresholdKeys::read` as an untrusted-input sink. Exploitation only requires flipping the `n`/`interpolation` bytes in a message; no secret knowledge, collusion, or privileged position is needed. Impact is a single-node resource-exhaustion DoS rather than key compromise or forgery — consistent with Medium.

### Recommendation
Validate parameters before performing length-derived work:

- Reorder `ThresholdKeys::read` so `ThresholdParams::new(t, n, i)` is constructed and checked immediately after reading `t`, `n`, `i` (line ~602), before the interpolation and `verification_shares` loops.
- Enforce the protocol's real participant bound (e.g., `MAX_PARTICIPANTS`) inside `ThresholdParams::new`, and read `verification_shares` only for `1 ..= params.n()` after that check.
- For `Interpolation::Constant`, confirm `t == n` (as `ThresholdKeys::new` does at line 369) *before* allocating `Vec::with_capacity(n)` and reading `n` scalars, or reject `Constant` on deserialization entirely if not needed.

### Proof of Concept
1. Craft a byte stream for `ThresholdKeys::<C>::read`: valid `C::ID` length + ID, then `t = 0xFFFF`, `n = 0xFFFF`, `i = 1` (little-endian `u16`s), interpolation byte `0x00` (Constant).
2. Append `65535` encoded scalars and `65535` encoded group elements (or truncate early — each loop iterates until `read_exact` fails, still consuming one decompression per element supplied).
3. Observe the function performs all `read_F`/`read_G` work and allocations before `ThresholdParams::new` at line 626 rejects the oversized `n` — whereas a correct implementation would reject after the first 6 bytes of parameters.

Caveat: I could not confirm the exact internal bound enforced by `ThresholdParams::new` (index excerpt truncated); the finding stands regardless, since the issue is that all element-reading work precedes that validation.