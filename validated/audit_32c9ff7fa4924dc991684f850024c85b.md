### Title
Unbounded pre-allocation in `ThresholdKeys::read` enables memory-exhaustion DoS from tiny attacker-controlled input - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` deserializes an attacker-controlled `n` (a `u16`, up to 65,535) and immediately performs `Vec::with_capacity(usize::from(n))` for `Interpolation::Constant`, allocating ~2 MiB of heap after consuming only ~9 bytes of input. A remote party that can feed crafted bytes into `ThresholdKeys::read` can force arbitrarily large allocations per call, achieving a high-amplification memory-exhaustion denial of service — the same class as the referenced Multer advisory (CWE-772, unbounded resource consumption triggered by untrusted input size fields).

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` reads `t`, `n`, and `i` as little-endian `u16`s directly from the reader, then reads a one-byte interpolation selector. When the selector is `0` (`Interpolation::Constant`), it executes:

```rust
let mut res = Vec::with_capacity(usize::from(n));
```

`Vec::with_capacity` commits the full allocation up front — for `n = 65535` and a 32-byte scalar `F::Repr`-sized element (e.g., a `k256`/`p256`/`ed25519` scalar), this is ~2 MiB — before a single scalar is read. The subsequent `for _ in 0 .. n { res.push(C::read_F(reader)?) }` loop and the `for l in (1 ..= n)` `read_G` loop only bound the *reads* to available bytes; the capacity allocation itself depends solely on the length claim. An attacker therefore supplies a ~9-byte prefix (`id_len`, `id` check is cheap; then `t=1`, `n=0xFFFF`, `i=1`, `interpolation=0`) and each call allocates ~2 MiB that is only released after `read_F` errors out. Issuing many such reads (e.g., one per connection/message) lets the attacker pin allocator memory or trigger allocation spikes proportional to request rate, not to bytes sent — the exact amplification structure of CWE-772 length-prefixed allocation bugs.

### Impact Explanation
Any integration that calls `ThresholdKeys::read` (or a wrapper that reaches it) on bytes influenced by an untrusted party — e.g., key-share material exchanged during DKG coordination or deserialized from untrusted storage/messages — can be driven into allocator pressure or OOM with negligible attacker bandwidth. The vulnerable lines:

- `crypto/dkg/src/lib.rs` lines 591–602: `t`, `n`, `i` read as raw `u16` from the reader.
- `crypto/dkg/src/lib.rs` lines 604–616: `interpolation` selector; `0 => Interpolation::Constant(Vec::with_capacity(usize::from(n)))` allocates `n` element slots before any input validation of `n` against the actual remaining data.
- `crypto/dkg/src/lib.rs` lines 620–623: `verification_shares` HashMap is populated by `n` `read_G` calls (bounded by actual bytes, so not itself exploitable, but compounds cost when data is present).

Amplification: ~9 attacker bytes → ~2 MiB allocation (~200,000× per call, repeatable).

### Likelihood Explanation
`ThresholdKeys::read` is a public deserialization entry point for threshold key material. Reachability depends on the integrator feeding untrusted bytes to it (which the threat model allows per the rules: untrusted bytes fed to `ThresholdKeys::read`). Threshold parameters are never sanity-checked before allocation — `ThresholdParams::new(t, n, i)` is only invoked *after* the `with_capacity` allocation and the read loops. `n` is capped at 65,535 by the `u16` encoding, which limits per-call amplification but does not prevent repeated-call exhaustion, consistent with a Medium-severity resource-exhaustion finding.

### Recommendation
Before allocating, bound `n` (and `t`) to a protocol maximum (e.g., `MAX_PARTICIPANTS`) and reject `n` values that exceed it. Prefer incremental `push` without `with_capacity`, or chunk the reads so allocation grows only as data actually arrives (as `networks/ethereum/src/machine.rs` `Call::read` already does with 1 KB chunks to defeat the identical bug class). Validate `ThresholdParams::new(t, n, i)` *before* any allocation keyed by `n`.

### Proof of Concept
```rust
use std::io;
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, Participant};
// Any in-scope ciphersuite, e.g. ciphersuite::Secp256k1
type C = ciphersuite::Secp256k1;

fn poc() -> io::Result<()> {
  let mut bytes = vec![];
  // C::ID length + ID (must match to pass the curve check)
  bytes.extend(&(C::ID.len() as u32).to_le_bytes());
  bytes.extend(C::ID);
  // t = 1, n = 65535, i = 1  -> attacker-controlled n
  bytes.extend(&1u16.to_le_bytes());      // t
  bytes.extend(&u16::MAX.to_le_bytes());  // n = 65535
  bytes.extend(&1u16.to_le_bytes());      // i
  bytes.push(0u8);                        // Interpolation::Constant
  // No scalar data follows: read_F will error, but Vec::with_capacity(65535)
  // has already allocated ~2 MiB for this ~C::ID.len()+13-byte input.
  // Repeated calls amplify memory pressure ~200,000x the attacker's bandwidth.
  let _ = ThresholdKeys::<C>::read(&mut bytes.as_slice());
  Ok(())
}
```

Each invocation allocates `65535 * size_of::<C::F>()` bytes of capacity from a ~50-byte message before failing on the missing scalar data, enabling sustained memory-exhaustion DoS against any service exposing this reader to untrusted input.