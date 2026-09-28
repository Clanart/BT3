### Title
Attacker-controlled `n` in `ThresholdKeys::read` triggers O(n²) field inversion/multiplication hang in `ThresholdKeys::new` - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574) deserializes `t`, `n`, and `i` directly from untrusted bytes, then calls `ThresholdKeys::new` (line 625). `ThresholdKeys::new` computes the group key by summing `interpolation_factor` over participants `1..=t` (lines 376–378). With `Interpolation::Lagrange`, each `interpolation_factor` call loops over all `t` included participants and performs a field inversion (lines 229–247), making group-key reconstruction O(t²). `t` and `n` are `u16` values bounded only by `ThresholdParams::new` (lines 166–179), which permits `t == n == 65535`. An attacker supplying ~4 MB of well-formed bytes causes ~4.3 billion field multiplications and 65535 field inversions — a hang matching the complete-DOS bug class of CVE-2018-3283.

### Finding Description
The reachable path from untrusted bytes:

- `ThresholdKeys::read` reads `t`, `n`, `i` as raw `u16`s, then reads `n` scalars or the Lagrange tag, `n` verification shares via `read_G`, and calls `ThresholdParams::new(t, n, i)` → `ThresholdKeys::new` (crypto/dkg/src/lib.rs:591–631).
- `ThresholdParams::new` only rejects `t == 0`, `n == 0`, `t > n`, and `i > n` (lines 166–179). There is no sanity bound on `n`.
- `ThresholdKeys::new` builds `t = 1..=t` and computes `verification_shares[i] * interpolation_factor(*i, &t)` summed over all `t` participants (lines 376–378).
- For `Interpolation::Lagrange`, `interpolation_factor` iterates the full `included` list (length `t`) per participant and calls `denom.invert().unwrap()` (lines 229–247). Total work is t iterations × t multiplies each + t inversions.

With `t = n = 65535` (encodable in 2 bytes each), the reader supplies `65535` scalar/point encodings (~4 MB total) and `ThresholdKeys::new` performs ~4.3×10⁹ field multiplications plus 65535 inversions — orders of magnitude more work than the input size, a computational-amplification hang.

### Impact Explanation
Any context that feeds adversary-supplied bytes to `ThresholdKeys::read` (key-share blobs transmitted between validators, backups, or recovery payloads) can be made to hang inside `ThresholdKeys::new` before returning. This is a complete denial of service of the calling process for a duration proportional to t², analogous to the MySQL hang/crash (availability-only, CVSS 4.4) in the external report. No secret leakage or signature forgery results.

### Likelihood Explanation
Reachability depends on an integrator deserializing `ThresholdKeys` from a peer- or attacker-influenced source, which the rules designate as an in-scope untrusted-bytes sink. The payload is trivially constructible: header + `t = n = i = 65535` (with `i` as a valid `Participant`) + Lagrange tag byte + `n` valid scalar encodings + `n` valid point encodings. No privileges, timing, or probabilistic conditions are required — the quadratic cost is deterministic once `n` is accepted.

### Recommendation
Enforce a protocol-realistic upper bound on `n`/`t` in `ThresholdParams::new` or at the top of `ThresholdKeys::read` (e.g., reject `n` above the maximum validator set size Serai actually supports, well below 65535). Alternatively, memoize the Lagrange numerator product (`num` is the same for all `i` up to a factor of `i`/`share`) or compute all interpolation factors in O(t) via prefix/suffix products, eliminating the quadratic blowup regardless of `n`.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs — ThresholdKeys::read -> ThresholdKeys::new
// Construct a serialized ThresholdKeys blob for curve C with:
//   id_len/id valid, t = 0xFFFF, n = 0xFFFF, i = 1,
//   interpolation byte = 1 (Lagrange),
//   secret_share = any valid F encoding,
//   verification_shares = 65535 valid G encodings.
//
// ThresholdParams::new(0xFFFF, 0xFFFF, Participant(1)) succeeds.
// ThresholdKeys::new then runs, for each i in 1..=65535:
//   interpolation_factor(i, [1..=65535]) -> 65534 field muls + 1 inversion
// Total: ~4.3e9 field multiplications + 65535 inversions — process hang.

let mut buf = vec![];
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
buf.extend(0xFFFFu16.to_le_bytes());  // t
buf.extend(0xFFFFu16.to_le_bytes());  // n
buf.extend(1u16.to_le_bytes());       // i
buf.push(1);                          // Interpolation::Lagrange
buf.extend(C::F::ONE.to_repr().as_ref());
for _ in 0 .. 0xFFFFu16 {
  buf.extend(C::generator().to_bytes().as_ref()); // ~4 MB total input
}
let _ = ThresholdKeys::<C>::read(&mut buf.as_slice()); // hangs in new()
```