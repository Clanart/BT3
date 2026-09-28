### Title
`ThresholdKeys::read` performs unbounded, attacker-controlled deserialization work before validating parameters — ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` reads an attacker-controlled `n` (a `u16` field) and uses it to drive two unbounded loops — `n` scalar reads for `Interpolation::Constant` and `n` group-element decodings for `verification_shares` — plus a `Vec::with_capacity(n)` allocation, all *before* `ThresholdParams::new` validates `t`/`n`/`i`. An unprivileged party supplying a blob with `n = u16::MAX` forces ~65,535 `read_F`/`read_G` operations (each `read_G` a full curve decompression) and large allocations, even though the parameters are later rejected or the honest set is orders of magnitude smaller. This mirrors the report's uncapped-resource pattern: an attacker-influenced quantity drives work far beyond the legitimate bound with no cap enforced at decode time.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` deserializes `t`, `n`, `i` as raw `u16`s at lines 591–602, then:

- reads `n` scalars into `Interpolation::Constant` with `Vec::with_capacity(usize::from(n))` (lines 607–613),
- reads `n` points into `verification_shares` via `C::read_G` (lines 620–623),

and only afterwards calls `ThresholdParams::new(t, n, i)` inside the `ThresholdKeys::new` invocation (lines 625–631). There is no check bounding `n` against the expected validator-set size, and no early rejection: the expensive deserialization of up to 65,535 field elements and 65,535 group elements happens unconditionally. `ThresholdParams::new` (lines 166–179) only enforces `t != 0`, `n != 0`, `t <= n`, `i <= n` — it never caps `n`, so `n = 65535` is accepted as long as `t <= n` and `i <= n`. Every sentence of this path is reachable purely from untrusted bytes handed to `ThresholdKeys::read`, an explicitly allowed input surface.

### Impact Explanation
Each `C::read_G` performs a full group-element decode (decompression + subgroup/canonicity checks) — orders of magnitude more expensive than a byte copy. A ~2 MB attacker-supplied buffer therefore induces ~65,535 point decompressions plus a 65,535-entry `HashMap` and a `Vec::with_capacity` of that size, on a code path used to (re)load threshold keys. Where this reader sits on a message-handling path, repeated crafted blobs exhaust CPU on a victim validator/coordinator — a direct analog of the report's DoS where uncapped input quantity forces work that aborts/exhausts the operation. Honest Serai sets are bounded (e.g., `MAX_KEY_SHARES_PER_SET`), so legitimate `n` is far smaller; the attacker-controlled count has no such bound here.

### Likelihood Explanation
Likelihood is Medium: the bug requires `ThresholdKeys::read` to be exposed to attacker-influenced bytes (listed as an in-scope untrusted surface). The payload is trivially constructible — a valid curve ID header, `t = 1`, `n = 0xFFFF`, `i = 1`, `Lagrange` tag, one scalar, then ~65,535 validly-encoded points — and produces a *valid* `ThresholdKeys` (since `t <= n`, `i <= n`), so the work is incurred in full rather than erroring early. Impact is capped at per-message CPU/memory exhaustion rather than key compromise, keeping this Medium rather than High.

### Recommendation
- Validate `t`, `n`, `i` via `ThresholdParams::new` **immediately after** reading them, before any length-driven loops, so `t > n` / `i > n` blobs are rejected before allocation.
- Enforce a protocol-level maximum on `n` (e.g., `MAX_KEY_SHARES_PER_SET`) and reject `n` exceeding it in `read`.
- For `Interpolation::Constant`, additionally require `coefficients.len() == n` consistency and prefer reading through a bounded reader / checking remaining length against `n * repr_size` before looping.

### Proof of Concept
```rust
// Attacker-controlled bytes for ThresholdKeys::<Ristretto>::read
let mut blob = vec![];
blob.extend((Ristretto::ID.len() as u32).to_le_bytes());
blob.extend(Ristretto::ID);                       // curve ID match
blob.extend(1u16.to_le_bytes());                  // t = 1
blob.extend(u16::MAX.to_le_bytes());              // n = 65535 (uncapped)
blob.extend(Participant::new(1).unwrap().to_bytes()); // i = 1
blob.push(1);                                     // Interpolation::Lagrange
blob.extend(Ristretto::read_F-sized zero scalar); // secret_share
// 65535 valid encoded group elements:
for _ in 0..u16::MAX {
    blob.extend(Ristretto::generator().to_bytes()); // any valid repr
}
// ThresholdKeys::read decompresses all 65,535 points and allocates a
// 65,535-entry HashMap before/without any cap, then SUCCEEDS since
// t <= n and i <= n.
let keys = ThresholdKeys::<Ristretto>::read(&mut blob.as_slice());
```
The loop at `crypto/dkg/src/lib.rs:620-623` iterates `1 ..= n` with `n` fully attacker-controlled, and validation at lines 625–631 accepts `n = 65535`, confirming the amplification is realized rather than rejected.