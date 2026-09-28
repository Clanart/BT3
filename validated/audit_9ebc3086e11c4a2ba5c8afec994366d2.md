### Title
Attacker-controlled participant count in `ThresholdKeys::read` forces a large pre-allocation before the declared bytes arrive (memory amplification) - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` trusts a two-byte `n` field from the input stream to size a `Vec::with_capacity` allocation *before* any of the `n` field elements have been read, and before `ThresholdParams::new` validates `t`/`n`/`i`. A ~15-byte input can therefore force a ~2 MiB intermediate allocation; repeated submissions of such inputs create unbounded peak memory pressure, the same decompression/length-amplification class as the referenced HTTPX2 advisory (small attacker input → large intermediate allocation).

### Finding Description
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632), after the curve-ID check, the reader consumes `t`, `n`, and `i` as raw `u16`s (lines 591-602) and then deserializes the `Interpolation` variant. For `Interpolation::Constant`, the code executes:

```rust
// crypto/dkg/src/lib.rs:608
let mut res = Vec::with_capacity(usize::from(n));
for _ in 0 .. n {
  res.push(C::read_F(reader)?);
}
```

`with_capacity(usize::from(n))` performs an allocation proportional to the attacker-chosen `n` (up to `u16::MAX` = 65,535) immediately. `C::read_F` then fails on the first missing element if the input is short — but the allocation already happened. `ThresholdParams::new(t, n, i)` — the only check that would constrain `n` — is called *after* the read, at line 626, so nothing bounds `n` before the allocation. A sender only needs to transmit ~15 bytes (`id_len` + `C::ID` + 6 bytes of params + 1 tag byte) to trigger an allocation of `65535 × size_of::<C::F>` (≈2 MiB for 32-byte fields), an amplification factor of roughly 10⁵ per call.

Contrast with code in this codebase that explicitly guards this class: `RrCodec::read_request` checks the length against a maximum before allocating (coordinator/src/p2p.rs:264-270), `Transaction::read` for `DkgCommitments` bounds `commitments_len * each_commitments_len` before allocating (coordinator/src/tributary/transaction.rs:286-294), and `Call::read` reads in 1 KB chunks specifically to avoid "a valid DoS ... to claim a 4 GB data is present for only 4 bytes" (networks/ethereum/src/machine.rs:51-60). `ThresholdKeys::read` has no equivalent guard.

Note: the allocation is freed when `read_F` errors, so this is a *peak-memory* amplification, not a persistent leak. Impact requires many concurrent/rapid `read` calls (e.g., parallel session attempts or a caller retrying on attacker-supplied bytes) to stack transient spikes into OOM. This limits severity relative to a per-connection persistent blowup.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (an explicitly untrusted deserialization surface in the DKG/key-loading path) can cause disproportionate peak memory usage: ~2 MiB allocated per ~15-byte input, before any data-size validation or parameter validation runs. Under repeated or concurrent submissions — e.g., many crafted payloads in one session or across retries — the node can experience severe memory pressure or OOM termination during key/threshold-key deserialization, denying service to the signing/resharing pipeline. This mirrors the advisory class: bounded wire bytes producing unbounded intermediate allocation.

### Likelihood Explanation
Medium. The path is a `pub fn read<R: io::Read>` on a core threshold type reachable wherever serialized `ThresholdKeys`/`ThresholdCore` bytes are ingested, and the trigger is trivially crafted (a handful of bytes with `n = 0xFFFF` and interpolation tag `0`). The amplification per call (~10⁵×) is modest compared to the 1000×-per-chunk ratio in the advisory, and the memory is transient (freed on read failure), so meaningful DoS requires repetition or concurrency rather than a single message. No validation of `n` against `t`, a maximum participant bound, or the actual remaining stream length exists before allocation.

### Recommendation
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read`:

- Construct and validate `ThresholdParams` *before* deserializing the `Interpolation` variant, and reject `n` above a sane protocol bound (real multisig sets are far below 65,535) before any length-dependent allocation.
- Replace `Vec::with_capacity(n)` with a plain `Vec::new()` + `push` loop (allocate only as elements actually arrive), or cap the pre-allocation at a small constant — the same chunked-read pattern already used in `networks/ethereum/src/machine.rs`.
- Optionally use a `reader.take(remaining_bytes)`-style bound so total work is proportional to bytes actually supplied.

### Proof of Concept
```rust
use std::io;
use dkg::{ThresholdKeys};
use ciphersuite::Ciphersuite;

// C: any in-scope Ciphersuite (e.g., Ristretto)
fn poc<C: Ciphersuite>() {
    // id_len + C::ID
    let mut buf = Vec::new();
    buf.extend(&(C::ID.len() as u32).to_le_bytes());
    buf.extend(C::ID);

    // t = 1, n = u16::MAX (attacker-chosen), i = 1
    buf.extend(&1u16.to_le_bytes());
    buf.extend(&u16::MAX.to_le_bytes());
    buf.extend(&1u16.to_le_bytes());

    // interpolation tag 0 => Interpolation::Constant, triggering
    // Vec::with_capacity(usize::from(n)) before any F is read
    buf.push(0u8);

    // Total input: ~15 bytes + len(C::ID). No field elements follow.
    // ThresholdKeys::read allocates ~65535 * size_of::<C::F> (~2 MiB)
    // inside crypto/dkg/src/lib.rs:608 before C::read_F hits EOF.
    let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
}
```

Trigger: send the crafted serialization to any code path calling `ThresholdKeys::<C>::read` on untrusted bytes; each invocation performs the ~2 MiB pre-allocation up front regardless of how few bytes were actually provided.