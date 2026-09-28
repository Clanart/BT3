### Title
Unbounded attacker-controlled allocation in `ThresholdKeys::read` enables memory-exhaustion DoS - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` in `crypto/dkg` deserializes a `u16` participant count `n` directly from the input stream and immediately performs `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` share vector and iterates `n` times building the `verification_shares` `HashMap` — all before any bound is placed on `n` relative to actual data available. Like CVE-2019-15722 (crafted input exhausting client resources), a small untrusted byte string can force large allocations and, when invoked repeatedly, exhaust memory of any verifier or node deserializing `ThresholdKeys`.

### Finding Description
In `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`):

- `t`, `n`, and `i` are each read as raw `u16`s from the stream (lines 591-602), so `n` is fully attacker-controlled and can be up to 65535.
- If the interpolation tag is `0` (`Interpolation::Constant`), the code executes `Vec::with_capacity(usize::from(n))` (line 608) — an immediate allocation of `n * size_of::<C::F>()` bytes (≈2 MiB for a 32-byte scalar with `n = 65535`) driven by a mere ~10 bytes of attacker input, before `ThresholdParams::new` is ever consulted to bound `n`.
- It then loops `1 ..= n` inserting `C::read_G(reader)` results into `verification_shares` (lines 620-623), and only afterwards calls `ThresholdParams::new(t, n, i)` (line 626) which would have rejected nonsensical parameters — but by then the allocation and any partial work is already done.

Because `Vec::with_capacity` commits memory up front, an attacker need only supply the short header bytes; the loop's subsequent `read_exact` failures abort early, but the oversized allocation has already been performed. Repeated submissions (e.g., via any `ThresholdKeys::read` / DKG message path that accepts untrusted bytes) amplify this into sustained memory exhaustion.

### Impact Explanation
Any component that calls `ThresholdKeys::read` on bytes influenced by an unprivileged party (key-share delivery, recovery, or DKG message handling) can be forced into repeated multi-megabyte allocations from negligible input, degrading or crashing the signer/verifier — an availability loss matching the report's bug class (C:N/I:N/A:H).

### Likelihood Explanation
Reachability depends on `ThresholdKeys::read` being exposed to untrusted input; the prompt's own rules list it as a target deserialization surface. The amplification factor (~2 MiB per ~10 bytes) is high and repeatable, so exploitation is cheap where the surface is reachable. Uncertainty: I could not confirm within the allowed searches an in-scope call site that feeds purely attacker-controlled bytes into `ThresholdKeys::read` rather than locally persisted trusted data; if it is only ever used on trusted storage, the practical severity drops.

### Recommendation
- Read and validate `(t, n, i)` through `ThresholdParams::new` (or an equivalent bound) *before* allocating; reject `n` exceeding the protocol's participant bound prior to `with_capacity`.
- Replace `Vec::with_capacity(n)` with incremental `push` and let the stream length bound actual work, or cap `n` at a small constant (e.g., 255) matching maximum validator-set size.
- Perform the same reorder for the `verification_shares` loop so `n` is validated before iterating.

### Proof of Concept
```rust
// crypto/dkg — conceptual PoC
// Craft a ThresholdKeys blob with n = 0xFFFF:
//   [id_len(4) || C::ID || t(2) || n=0xFFFF(2) || i(2) || interpolation=0x00]
// At line 608: Vec::with_capacity(65535) of C::F (~2 MiB) is allocated
// from ~11 attacker bytes, before ThresholdParams::new validates n.
// Looping this input against ThresholdKeys::read exhausts memory.
```
Send repeated serialized blobs with the interpolation tag `0` and `n = u16::MAX`; each invocation allocates ≈2 MiB regardless of how few bytes follow, enabling memory exhaustion of the deserializing party.