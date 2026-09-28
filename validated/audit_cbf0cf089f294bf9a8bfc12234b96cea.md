### Title
Attacker-controlled length in `ThresholdKeys::read` triggers unbounded pre-allocation before input validation - (File: crypto/dkg/src/lib.rs)

### Summary
The ImageMagick advisory describes a missing check of the permitted allocation limit before memory is reserved from attacker-influenced parameters. The same class appears in Serai: `ThresholdKeys::read` in `crypto/dkg/src/lib.rs` reads `t`, `n`, and `i` directly from the input byte stream, then uses the attacker-controlled `n` to size `Vec::with_capacity` and to drive deserialization loops — before `ThresholdParams::new` validates `t <= n` or any of the keyed data is verified.

### Finding Description
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632), deserialization proceeds as:

```rust
// crypto/dkg/src/lib.rs:591-613
let (t, n, i) = {
  let mut read_u16 = || -> io::Result<u16> { ... };
  (read_u16()?, read_u16()?,
   Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?)
};
...
0 => Interpolation::Constant({
  let mut res = Vec::with_capacity(usize::from(n));
  for _ in 0 .. n { res.push(C::read_F(reader)?); }
  res
}),
...
let mut verification_shares = HashMap::new();
for l in (1 ..= n).map(Participant) {
  verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
}
ThresholdKeys::new(ThresholdParams::new(t, n, i)...)
```

- `n` is a raw `u16` from the wire; `Vec::with_capacity(usize::from(n))` at line 608 reserves `n * size_of::<C::F>()` (~2 MiB for a 32-byte scalar when `n = 65535`) immediately upon the interpolation tag byte `0`, before a single field element is read.
- The `verification_shares` loop then performs up to 65535 `read_G` calls and `HashMap` insertions, and `ThresholdKeys::new`/`interpolation_factor` at line 226-249 performs O(n) inversion/multiplication work, plus `group_key` summation over participants `1..=t` at line 376-378 — all indexed by attacker-chosen `n`/`t`, with consistency checks (`t <= n`, share count) applied only at the very end in `ThresholdKeys::new` (line 355).
- Unlike the guarded readers elsewhere in Serai (e.g., the `MAX_LIBP2P_REQRES_MESSAGE_SIZE` cap in `coordinator/src/p2p.rs:264`, or the chunked read in `networks/ethereum/src/machine.rs:51-60` with its explicit "valid DoS" comment), this reader imposes no allocation limit proportional to bytes actually present.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (the rules explicitly allow `ThresholdKeys::read` as an untrusted-bytes sink) can trigger allocations and CPU work far exceeding the input size: ~4 bytes of `n`/tag yield a multi-megabyte reservation plus a 65535-iteration read/insert path, and a fully populated message yields ~65535 point deserializations and `HashMap` inserts plus Lagrange inversion work. Repeated deserialization requests produce memory amplification and allocation churn consistent with the CVE's denial-of-service class. The allocation is transient (dropped on error), so the practical impact is memory-pressure/CPU DoS rather than persistent exhaustion — Medium at best, matching the source advisory's severity.

### Likelihood Explanation
Reachability depends on an integration calling `ThresholdKeys::read` on remote-supplied bytes rather than only on locally stored keys. Within the library itself the path is one short input away from the oversized reservation; the amplification factor is modest (u16 bound), which caps severity.

### Recommendation
Validate `ThresholdParams` (`t <= n`, `i <= n`) and impose a sane upper bound on `n` (e.g., `MAX_KEY_SHARES_PER_SET`) *before* allocating. Replace `Vec::with_capacity(n)` with incremental `push` on data actually read, or chunk the reads as done in `Call::read` (`networks/ethereum/src/machine.rs:53-60`).

### Proof of Concept
```rust
use std::io;
use ciphersuite::Ciphersuite;
use frost::curve::Ed25519;
use frost::dkg::ThresholdKeys;

fn main() {
  let mut buf = vec![];
  // C::ID len + ID for Ed25519
  buf.extend(u32::try_from(<Ed25519 as Ciphersuite>::ID.len()).unwrap().to_le_bytes());
  buf.extend(<Ed25519 as Ciphersuite>::ID);
  // t = 1, n = 65535, i = 1
  buf.extend(1u16.to_le_bytes());
  buf.extend(65535u16.to_le_bytes());
  buf.extend(1u16.to_le_bytes());
  // Interpolation::Constant tag -> Vec::with_capacity(65535) for C::F
  buf.push(0u8);
  // No field bytes follow: ~2 MiB reserved, then read fails
  let res: io::Result<ThresholdKeys<Ed25519>> = ThresholdKeys::read(&mut buf.as_slice());
  assert!(res.is_err()); // allocation already occurred
}
```