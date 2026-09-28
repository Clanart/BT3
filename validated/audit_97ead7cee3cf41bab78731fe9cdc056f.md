### Title
Attacker-controlled `t`/`n` fields in `ThresholdKeys::read` force quadratic interpolation work and large up-front allocations — ([File: crypto/dkg/src/lib.rs])

### Summary
The bug class in ALPINE-CVE-2024-12705 is resource exhaustion: a resolver floods CPU/memory by processing crafted inputs whose cost is disproportionate to their size. In Serai's in-scope code, `ThresholdKeys::<C>::read` (`crypto/dkg/src/lib.rs:574-632`) trusts two attacker-supplied `u16` fields (`t`, `n`) and then performs work proportional to `n` (allocation + per-participant point parsing) and work proportional to roughly `t²` in `ThresholdKeys::new`'s interpolation of the group key. A few bytes of crafted input therefore translate into multi-megabyte allocations and billions of field/point operations — the same "small crafted message, disproportionate compute" asymmetry as the BIND DoH flood, reachable via the rules' explicitly-listed `ThresholdKeys::read` entry point.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` directly from the stream (`crypto/dkg/src/lib.rs:591-602`) with no bound other than `u16::MAX = 65535`. It then:

- Allocates `Vec::with_capacity(usize::from(n))` for `Interpolation::Constant` before any bytes of the coefficients have been validated (`crypto/dkg/src/lib.rs:608`).
- Inserts `n` verification shares into a `HashMap`, reading a full group element per participant (`crypto/dkg/src/lib.rs:620-623`).
- Calls `ThresholdKeys::new(ThresholdParams::new(t, n, i), ...)`, which reconstructs `group_key` by interpolating over participants `1..=t`. Under `Interpolation::Lagrange` this evaluates O(t) Lagrange coefficients, each a product over O(t) participants — i.e. O(t²) scalar operations plus O(t) scalar multiplications of points, ~4.3×10⁹ field operations for `t = 65535`, triggered by a message under ~4 MB (or a truncated message for the allocation paths).

By contrast, the analogous length-prefixed readers that do bound themselves show the intended pattern: `Commitments::read` in PedPoP loops only `params.t()` from an already-validated `ThresholdParams` (`crypto/dkg/pedpop/src/lib.rs:110-128`), and `NonceCommitments::read`/`Commitments::read` in FROST are driven by the locally-known `generators` list, not the wire (`crypto/frost/src/nonce.rs:74-80`, `crypto/frost/src/nonce.rs:133-139`). `ThresholdKeys::read` is the outlier that sizes its work purely from untrusted bytes.

### Impact Explanation
An unprivileged party who can supply bytes to `ThresholdKeys::read` — listed as a reachable untrusted-bytes sink — can set `n = 65535` to force a ~2 MB `Vec` allocation plus a 65535-entry `HashMap` from a handful of bytes, and set `t`/`n` large to force O(t²) interpolation work inside `ThresholdKeys::new`. Repeated or concurrent submissions exhaust verifier memory and CPU, stalling the node processing the data. This is a pure availability impact analogous to the CVE (no secret recovery, no forgery), so it lands as Medium severity under the rules.

### Likelihood Explanation
Reachability requires a deployment path where serialized `ThresholdKeys` come from an untrusted source rather than the node's own locally-generated keys. Where `ThresholdKeys::read` is used on locally-produced bytes the attacker's only lever is mutating stored data, which is out of threat model. Where it is applied to bytes transmitted between parties (e.g., key material exchanged or relayed during DKG/recovery/promotion flows), a single malicious participant can trigger the cost in every honest party that parses the blob, which is the realistic scenario. No collusion or special privileges are needed.

### Recommendation
- Validate `t`/`n` against a protocol-level maximum (e.g., `MAX_KEY_SHARES_PER_SET`) before allocating or interpolating; reject early via `ThresholdParams::new` plus an explicit `n <= MAX` check.
- Replace `Vec::with_capacity(n)` and the upfront `HashMap` sizing with incremental `push`/`insert` so allocation tracks bytes actually received.
- For `Interpolation::Lagrange`, cap `t` before `ThresholdKeys::new` runs the O(t²) interpolation.

### Proof of Concept
```rust
// Conceptual; targets ThresholdKeys::<C>::read (crypto/dkg/src/lib.rs:574)
// Build a header claiming a maximal set, followed by minimal body.
let mut buf = vec![];
buf.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
buf.extend(C::ID);                 // must match the curve ID
buf.extend(u16::MAX.to_le_bytes()); // t = 65535
buf.extend(u16::MAX.to_le_bytes()); // n = 65535
buf.extend(1u16.to_le_bytes());    // i = 1
buf.push(1);                       // Interpolation::Lagrange -> no scalars needed on the wire
buf.extend(<C::F as PrimeField>::Repr::default().as_ref()); // secret_share
// then n group elements of verification_shares; even a truncated tail forces
// the Vec/HashMap sizing and, once full, the O(t^2) interpolation in
// ThresholdKeys::new — ~4.3e9 field ops from <5 MB of input.
let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
```

Note: exact per-participant byte cost depends on the ciphersuite's `GroupEncoding` size; the `t`/`n`-driven allocation and quadratic interpolation claims follow directly from `crypto/dkg/src/lib.rs:591-631` and the `Interpolation::Lagrange` path in `ThresholdKeys::new`. The concrete network entry point that pipes attacker bytes into `ThresholdKeys::read` was not fully traced within scope, which is the main residual uncertainty for likelihood.