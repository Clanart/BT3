### Title
`ThresholdKeys::read` performs an attacker-controlled pre-allocation of up to ~2 MiB per message before validating that the data exists - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::<C>::read` (crypto/dkg/src/lib.rs:573-632) trusts a serialized `n` field (u16, up to 65535) and immediately executes `Vec::with_capacity(usize::from(n))` when the `interpolation` byte selects `Interpolation::Constant`. This mirrors CVE-2016-2146: a length declared in untrusted input controls allocation size, and the allocation is committed before a single payload byte is read.

### Finding Description
In `ThresholdKeys::read` [1](#0-0) , after reading `t`, `n`, `i` (6 bytes) and the interpolation tag (1 byte), the code does:

```rust
0 => Interpolation::Constant({
  let mut res = Vec::with_capacity(usize::from(n));
  for _ in 0 .. n {
    res.push(C::read_F(reader)?);
  }
  res
}),
```

`n` is fully attacker-controlled and is never bound-checked against the remaining input length before `with_capacity` runs. Each `C::F` element is 32 bytes (or 56 for ed448), so an ~11-byte input forces an allocation of 65535 × 32 ≈ 2 MiB (≈ 3.7 MiB for Ed448). If the reader then hits EOF, the allocation is still performed and the error is returned only after the `with_capacity` call — so a truncated message still costs the full allocation. Repeated `ThresholdKeys::read` calls on hostile bytes therefore cause unbounded cumulative memory pressure and eventual OOM / abort, the same denial-of-service class as the CVE (worker process crash via unconstrained read-driven allocation).

A weaker secondary instance exists in `SchnorrAggregate::read` [2](#0-1) , which loops a claimed `u32` count, but it only `push`es after each successful `read_G`, so allocation stays proportional to actual input bytes and it does not amplify.

### Impact Explanation
`ThresholdKeys::read` is an explicitly-listed untrusted-byte sink. An unprivileged party who can feed bytes to a `read`/`sign`/`complete` path (e.g., serialized key material or any protocol message that routes through this deserializer) can force a ~170,000× byte amplification per call. Issued in a loop, this exhausts process memory and crashes the signer/verifier — a direct availability loss equivalent to the CVE's worker-process crash / memory consumption. For a FROST threshold signer this aborts signing rounds; memory exhaustion can also deadlock co-located processes as in the original advisory.

### Likelihood Explanation
The reachability depends on the integration passing unauthenticated bytes to `ThresholdKeys::read`; the prompt's threat model explicitly includes untrusted bytes fed to `ThresholdKeys::read`. No threshold of colluding validators or broken BFT is required — a single malformed byte stream of ~11 bytes triggers the 2 MiB allocation. Likelihood of exploitation wherever this sink is reachable is high; the constraint is that the attacker must be able to invoke deserialization repeatedly to convert a bounded per-call allocation into exhaustion.

### Recommendation
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:607-613), do not pre-allocate from `n`. Either drop the `Vec::with_capacity` (push-only growth bounds memory by bytes actually present), or validate `n` against `params` consistency and/or a hard cap before allocating, and consider wrapping the reader in `io::Read::take` / a `LimitedReader` sized to the expected message length so a lying length field cannot out-allocate the enclosing message. The same bounded-reader pattern already used elsewhere in the codebase (e.g., chunked reads with a size cap) should be applied to all length-driven allocations in in-scope `read` functions.

### Proof of Concept
```rust
use std::io::Cursor;
use ciphersuite::Ciphersuite;
use frost::{curve::Secp256k1, ThresholdKeys};

// Header accepted by ThresholdKeys::<Secp256k1>::read:
//   u32 id_len = Secp256k1::ID.len(), then ID bytes,
//   t = 1, n = 0xFFFF, i = 1, interpolation = 0 (Constant)
let mut buf = Vec::new();
let id = <Secp256k1 as Ciphersuite>::ID;
buf.extend((id.len() as u32).to_le_bytes());
buf.extend(id);
buf.extend(1u16.to_le_bytes());      // t
buf.extend(0xFFFFu16.to_le_bytes()); // n = 65535 -> Vec::with_capacity(65535)
buf.extend(1u16.to_le_bytes());      // i
buf.push(0u8);                       // Interpolation::Constant
// No further bytes: read_F fails on EOF, but the ~2 MiB allocation
// already happened before the first read.

let mut reader = Cursor::new(buf);
let _ = ThresholdKeys::<Secp256k1>::read(&mut reader); // Err(EOF) after ~2 MiB alloc
// Repeating this call amplifies attacker bytes into unbounded memory consumption.
```

Note: I verified this pattern directly in `crypto/dkg/src/lib.rs`. I did not fully verify `EncryptedMessage::read` in `crypto/dkg/pedpop/src/encryption.rs` or the rust-bitcoin `consensus_decode` internals used by `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`); the latter may delegate to `bitcoin`'s `MAX_VEC_SIZE`-capped decoding and could be an additional instance, but that could not be confirmed within the available iterations.

### Citations

**File:** crypto/dkg/src/lib.rs (L604-613)
```rust
    let mut interpolation = [0];
    reader.read_exact(&mut interpolation)?;
    let interpolation = match interpolation[0] {
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
```

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
  }
```
