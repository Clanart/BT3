### Title
Unbounded attacker-controlled participant count in `ThresholdKeys::read` forces oversized allocation before input validation - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574) trusts the `n` field deserialized from untrusted bytes and immediately performs `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` coefficients vector, then loops `n` times reading field elements and another `n` times reading verification shares. This mirrors the phpseclib OID-length bug class (CWE-400): a length/count field from untrusted input drives resource consumption before any bound check against the data actually present.

### Finding Description
In `ThresholdKeys::read`, `t`, `n`, and `i` are read from the byte stream as `u16` values (crypto/dkg/src/lib.rs:591-602). `ThresholdParams::new` is only invoked *after* the allocations and reads, at line 625-626. Before that validation:

- If the interpolation tag is `0`, `Vec::with_capacity(usize::from(n))` allocates capacity for up to 65,535 `C::F` elements (~2–4 MB for a 32-byte-plus field element) based solely on two attacker-controlled bytes (crypto/dkg/src/lib.rs:607-613).
- The verification-share loop `for l in (1 ..= n)` builds a `HashMap` and calls `C::read_G` per claimed participant (crypto/dkg/src/lib.rs:620-623). Point deserialization for curves such as Secp256k1/Ed448 involves decompression and subgroup/validity checks, so a short input padded with valid points forces expensive per-element work gated only by `n`.

Unlike the `Interpolation::Constant` path, note that `Interpolation::Lagrange` skips the coefficient vector but still performs `n` `read_G` calls. Either path lets a small, cheaply-constructed header (`n = 0xFFFF`) amplify into a multi-megabyte allocation plus up to 65k point/scalar deserializations per call, with no `n <= actual_bytes` sanity check and no cap tied to the real `t`/`n` relationship until after the work is done.

### Impact Explanation
`ThresholdKeys::read` is one of the explicitly reachable deserialization APIs for untrusted bytes. Repeated calls (e.g., during multisig setup, key handoff, or any ingestion of externally supplied key material) let an unprivileged party burn memory and CPU disproportionate to the bytes sent — a classic length-field resource-exhaustion DoS analogous to CVE-2024-27355, which caused phpseclib to allocate/process proportional to an attacker-controlled OID length.

### Likelihood Explanation
Reachability requires the integrator to call `ThresholdKeys::read` on bytes an attacker can influence — plausible for any flow importing key shares from peers or storage. The amplification is moderate (bounded by u16::MAX rather than u32::MAX), and each `read_G`/`read_F` still requires corresponding bytes to be present, so CPU cost is partially bounded by input size; however the `Vec::with_capacity(n)` allocation is *not* bounded by input size, giving a clean allocation-amplification primitive. Medium severity.

### Recommendation
- Validate `t`/`n`/`i` via `ThresholdParams::new` *before* allocating or looping (currently done last, at crypto/dkg/src/lib.rs:625).
- Replace `Vec::with_capacity(usize::from(n))` with incremental `push` or a sane cap derived from already-validated params.
- Consider rejecting `n` values inconsistent with the remaining readable length where the reader supports it.

### Proof of Concept
Construct a byte string for `ThresholdKeys::read` with: valid `C::ID` length + `C::ID`, `t = 0x0001`, `n = 0xFFFF`, `i = 0x0001`, interpolation byte `0x00`, followed by truncated/garbage bytes. The call performs `Vec::with_capacity(65535)` (multi-MB allocation) purely from the header before any `ThresholdParams` validation. Repeating this against an endpoint that deserializes attacker-supplied `ThresholdKeys` amplifies a ~40-byte payload into megabytes of allocation per request.