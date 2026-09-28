### Title
Unbounded attacker-controlled signature count in `SchnorrAggregate::read`/`verify` enables CPU-exhaustion denial of service - (File: crypto/schnorr/src/aggregate.rs)

### Summary
CVE-2016-10723 is a resource-exhaustion bug class: an unprivileged party wastes CPU resources to degrade or lock up a system (availability-only, CVSS 5.5 Medium). The analog in Serai is `SchnorrAggregate::read` and `SchnorrAggregate::verify` in `crypto/schnorr/src/aggregate.rs`: a 4-byte length field controls how many group elements are deserialized and how many transcript expansions + scalar multiplications are performed, with no upper bound beyond `u32::MAX`. An unprivileged party feeding an oversized aggregate signature to the read/verify path forces expensive per-element work (point decompression on read; a `weight()` challenge derivation and a multiexp pair per element on verify) from a tiny attacker input, exhausting the verifier's CPU.

### Finding Description
`SchnorrAggregate::read` reads a `u32` count `len` and then calls `C::read_G(reader)` in a loop `0 .. u32::from_le_bytes(len)` (lines 77–87). Each `read_G` performs full point decompression/validation — the most expensive primitive-level operation per input byte in the codebase (~32–64 bytes of input buys a field square-root/inversion plus subgroup/identity checks). There is no cap on `len` (unlike `ThresholdKeys::read`, which is bounded by `u16`, or `Commitments::read` in PedPoP, which is bounded by `params.t()`), so the attacker alone decides the work factor.

If the signature is then verified, `SchnorrAggregate::verify` (lines 127–146) repeats the amplification: for every entry in `keys_and_challenges` it runs `weight()` (lines 22–65), which performs transcript challenge generation plus a bit-by-bit scalar accumulation loop (`for _ in 0 .. WORD_LEN_IN_BITS { res += res; }` per 64-bit word over `(F::NUM_BITS + 128)` bits), and pushes two pairs into a `multiexp_vartime` call. Per ~64 bytes of attacker input, the verifier burns a decompression, a wide scalar reduction, and two multiexp terms — orders of magnitude more work than the input size.

This mirrors CVE-2016-10723's shape: a public, unauthenticated input controls how much CPU a victim expends, with no fairness/bounding mechanism in the code path.

### Impact Explanation
Any service that accepts aggregate Schnorr signatures (or serialized `SchnorrAggregate` blobs) from untrusted parties — e.g., for batched signature verification — can be forced to spend arbitrarily large CPU per request by supplying a count of millions of `R` points with the corresponding (relatively cheap-to-produce) byte stream. A handful of concurrent submissions can starve signing rounds or verification pipelines, stalling threshold signing progress — a permanent-ish availability loss analogous to the kernel lock-up in the CVE. Consistent with the source CVE, this is a Medium-severity, availability-only issue: no secret or integrity impact, but denial of service reachable by an unprivileged party with public inputs.

### Likelihood Explanation
Exploitation only requires submitting bytes to a `SchnorrAggregate::read`/`verify` consumer — no key material, no validator status, no collusion. The amplification factor (one decompression + weight derivation + two MSM terms per ~64 bytes) makes even small payloads costly. Whether it is reachable in production depends on whether integrators deserialize/verify aggregates from untrusted sources, but the primitive itself provides no defensive bound, so any such consumer is vulnerable by default.

### Recommendation
- Enforce a sane maximum signature count in `SchnorrAggregate::read` (reject `len` above a protocol-defined bound before looping), matching the bounding style already used elsewhere (`params.t()` in `Commitments::read`, `u16` params in `ThresholdKeys::read`).
- Optionally reserve `Vec::with_capacity` and pre-check `len * Repr::size()` against a maximum serialized size before allocating/decoding.
- In `verify`, reject `keys_and_challenges` lengths above the same bound before running `weight()`/`multiexp_vartime`.

### Proof of Concept
Conceptual: construct a byte buffer `[len: u32 LE][len * C::G::Repr bytes][C::F::Repr bytes]` with `len = 1_000_000` and feed it to `SchnorrAggregate::<C>::read`, then call `verify(dst, &keys_and_challenges)` with a matching-length `keys_and_challenges` slice of identity/identity pairs. `read` performs ~1M point decompressions; `verify` performs ~1M `weight()` derivations and a 2M-term `multiexp_vartime`, all driven by an input on the order of ~64 MB (or far smaller counts for proportionally smaller but still unbounded cost). No check in `read` or `verify` bounds `len`, so the CPU expenditure scales linearly with attacker-chosen input with no fairness mechanism — the direct analog of CVE-2016-10723's CPU-starvation primitive.

Caveat: reachability depends on an in-scope consumer passing untrusted bytes to `SchnorrAggregate::read`/`verify`; within the scoped crates, the primitive itself is the unbounded component.