### Title
Quadratic CPU/memory cost in MuSig binding-factor derivation enables denial of service from attacker-controlled key lists - (File: crypto/dkg/musig/src/lib.rs)

### Summary
The body-parser advisory (CVE-2025-13466, CWE-400) describes a payload that stays within a modest size limit but forces CPU/memory work superlinear in the input size. `crypto/dkg/musig` has the same shape: `musig_key_multiexp`, `musig_key`, `musig_key_vartime`, and `musig` re-hash a transcript containing *all* `n` serialized keys once per key, because `binding_factor(transcript.clone(), i)` copies the full `context || keys_len || keys[0..n]` byte string and hashes it in `C::hash_to_F` for every index `i`. Input of ~2 MB (65 535 keys, the u16 maximum allowed by `check_keys`) therefore triggers ~130 GB of hashing plus ~130 GB of `Vec::clone` copying — quadratic work per linear input, reachable from untrusted bytes fed through `C::read_G` into the public `musig*` APIs.

### Finding Description
`binding_factor_transcript` builds one byte string `context || keys_len || key[0] || ... || key[n-1]` (musig/src/lib.rs:65-79). `binding_factor` then clones that whole vector and hashes it for each key index (musig/src/lib.rs:81-84), and the loop in `musig_key_multiexp` (lines 93-96) and `musig` (lines 139-146) invokes it `n` times. Total bytes hashed is Θ(n²): each `hash_to_F` call digests ~`32 + 2 + n·L` bytes (L = `to_bytes()` length, e.g. 32 for secp256k1, 57 for ed448).

`check_keys` only enforces `keys.len() <= u16::MAX` (lines 46-63) — no tighter bound. The parse path is `C::read_G` per key (ciphersuite/src/lib.rs:91-101), so a `keys` vector built from untrusted bytes costs the attacker only `n·L` wire bytes while the verifier performs `n` full-transcript hashes. For `n = 65535`, transcript size ≈ 2.1 MB and the total digest/cloning volume is ≈ 137 GB.

Additionally, `musig` (release builds) and `musig_key` each call `multiexp` over all `n` pairs — linear, and secondary to the quadratic hash cost.

### Impact Explanation
A single ≤2 MB message causes seconds-to-minutes of CPU saturation and multi-MB transient allocations in any service that computes MuSig aggregate keys over externally supplied key lists (the same service-slowdown/partial-outage profile as GHSA-wqch-xfxh-vrr4). Because the cost is quadratic, increasing `n` sharply amplifies the asymmetry: at `n = 65535` the hash work alone is ~70 000× the input size.

### Likelihood Explanation
Reachability depends on the integrator passing attacker-influenced key vectors into `musig`/`musig_key`/`musig_key_vartime` — which is exactly how these non-interactive DKG APIs are consumed (participant pubkey lists read via `read_G`/`Commitments::read` and handed to the aggregation). The library itself imposes no cap below 65 535 and no metering, so any such call path is exposed. No secret material, malformed curve data, or protocol misbehavior is required — only a long, valid key list, making this triggerable by an unprivileged party whose public key (or proposed set) reaches the aggregation.

### Recommendation
- Cap the key count at a protocol-meaningful bound (e.g. `n <= 1024`) inside `check_keys`, or document/enforce it at the API boundary.
- Eliminate the per-index `transcript.clone()`: hash incrementally with a `Transcript`/`Digest` that absorbs the key list once and derives each binding factor cheaply (e.g. `H(transcript_state || i)`), restoring Θ(n) total work.
- Alternatively, precompute the transcript hash once and include `i` via challenge extension, mirroring `dleq`'s `challenge` extension pattern (dleq/src/lib.rs:81).

### Proof of Concept
Conceptual (Rust, generic over `C`):

```rust
// Attacker-controlled input: n serialized points, n <= u16::MAX.
let n = u16::MAX as usize; // 65_535 keys, ~2.1 MB on the wire
let mut keys = Vec::with_capacity(n);
for _ in 0..n {
    // Each read_G succeeds on any valid encoding the attacker supplies.
    keys.push(<C as Ciphersuite>::read_G(&mut attacker_bytes)?);
}

// One call -> n clones+hashes of a ~2.1 MB transcript:
// binding_factor(transcript.clone(), i) for i in 1..=n
let _agg = musig_key_vartime::<C>(context, &keys)?;
// ~137 GB hashed + ~137 GB memcpy from Vec::clone => DoS
```

Instrumenting `binding_factor_transcript`/`binding_factor` confirms total hashed bytes grow as Θ(n²): hashing `32 + 2 + n·32` bytes `n` times. At `n = 65_535` this is ≈ 1.37×10¹¹ bytes digested for a ≈ 2.1 MB input — a clear CWE-400 asymmetric resource-consumption analog of the body-parser finding.