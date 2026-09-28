### Title
Attacker-controlled allocation amplification in `ThresholdKeys::read` enables memory-consumption DoS - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The external report (CVE-2019-18811) describes a memory-consumption DoS where attacker-triggered error paths allocate resources without bound. The analog in Serai exists in `ThresholdKeys::read`: the participant count `n` is read from untrusted bytes and used to drive a `Vec::with_capacity(n)` allocation and an `n`-iteration deserialization loop *before* `n` is validated by `ThresholdParams::new` at the end of the function.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` reads `t`, `n`, and `i` from the input (lines 591-602), then reads the interpolation method tag (line 604). If the tag is `0` (`Interpolation::Constant`), it executes `Vec::with_capacity(usize::from(n))` (line 608) and loops `0 .. n` calling `C::read_F` (lines 609-611). Separately, it then loops `(1 ..= n)` calling `read_G` into a `HashMap` (lines 620-623). Only *after* all of this does it call `ThresholdParams::new(t, n, i)` (line 626), which is the first point `t <= n` and `i <= n` are checked — and no upper bound on `n` itself is ever enforced.

Because `n` is a `u16` directly from the byte stream, a ~10-byte prefix (`id_len`, `id`, `t`, `n = 0xFFFF`, `i`, `interpolation = 0`) causes `Vec::with_capacity(65535)` — roughly 2-4 MB of heap allocated depending on the curve's field size — before any data needs to actually exist. The matching kernel-class weakness is that the expensive work happens up-front on attacker-declared sizes and only unwinds on failure; the caller gets an error after the allocation pressure and (for the `1 ..= n` loop) after up to 65535 decompression attempts each consuming only the bytes present.

Unlike the SignData/DkgCommitments readers in the coordinator, which gate deserialization on `TRANSACTION_SIZE_LIMIT`, this function applies no size limit derived from the actual remaining input length.

### Impact Explanation
An unprivileged party who can supply bytes to `ThresholdKeys::read` (listed in-scope as an untrusted-input sink) can force each call to perform a multi-megabyte allocation with a ~10-byte input — an amplification factor of roughly 10⁵. Issuing these in a loop (e.g., against any endpoint, RPC handler, or import path that deserializes threshold keys) causes sustained memory pressure and allocation churn, a denial of service matching the Medium-severity class of the reference CVE. There is no secret leakage, but availability impact is concrete and reachable with public input only.

### Likelihood Explanation
`read_F`/`read_G` on short buffers return errors quickly, so a single call is cheap; however the `with_capacity(65535)` allocation itself succeeds immediately regardless of input size, and the `0 .. n` loop still performs up to 65535 attempted reads before unwinding. Any integrator-facing path that accepts serialized `ThresholdKeys` (key import, message payloads, session resumption) is reachable by an unauthenticated or low-privilege caller, making repeated exploitation straightforward. No privileged position is required.

### Recommendation
Apply the same pattern used elsewhere in the codebase (e.g., `Transaction::read` checking `commitments_len * each_commitments_len` against `TRANSACTION_SIZE_LIMIT`, and `networks/ethereum/src/machine.rs` chunked reads):

- Validate `t`, `n`, `i` via `ThresholdParams::new` immediately after reading them, before allocating anything.
- Replace `Vec::with_capacity(usize::from(n))` with incremental `push` (or cap `n` at a protocol-meaningful maximum, e.g., a documented `MAX_PARTICIPANTS`), so allocation is proportional to bytes actually consumed.
- Optionally bound total readable size by checking the reader's remaining length before entering the `1 ..= n` loop.

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use dkg::ThresholdKeys;

fn poc<C: Ciphersuite>() {
  let mut bytes = vec![];
  // C::ID length + ID
  bytes.extend(&u32::try_from(C::ID.len()).unwrap().to_le_bytes());
  bytes.extend(C::ID);
  // t = 1, n = 65535 (max u16), i = 1
  bytes.extend(&1u16.to_le_bytes());
  bytes.extend(&u16::MAX.to_le_bytes());
  bytes.extend(&1u16.to_le_bytes());
  // interpolation = 0 (Constant) -> Vec::with_capacity(65535) of C::F
  bytes.push(0);

  // Each call allocates ~n * size_of::<C::F>() bytes before erroring on the
  // (absent) coefficient data. Looping this exhausts memory via the
  // allocator even though each call returns Err.
  loop {
    let _ = ThresholdKeys::<C>::read(&mut bytes.as_slice());
  }
}
```

The root cause is at `crypto/dkg/src/lib.rs:608` (`Vec::with_capacity(usize::from(n))`) and `crypto/dkg/src/lib.rs:621` (`for l in (1 ..= n)`), both consuming the unvalidated attacker-supplied `n` before `ThresholdParams::new` is invoked at line 626.