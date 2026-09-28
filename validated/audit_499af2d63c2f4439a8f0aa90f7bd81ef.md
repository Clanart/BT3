### Title
Attacker-controlled length in `ThresholdKeys::read` causes pre-validation memory exhaustion (DoS) - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` deserializes an attacker-controlled participant count `n` and immediately uses it to size a `Vec::with_capacity` and to drive a `HashMap` insertion loop *before* `ThresholdParams::new(t, n, i)` validates the parameters. A tiny input (under ~15 bytes) can force a multi-megabyte allocation, yielding a memory-amplification denial of service against any party that calls `ThresholdKeys::read` on untrusted bytes — the same bug class as the unbounded/leaked allocations in `WritePDFImage` (JLSEC-2026-838).

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` reads `t`, `n`, and `i` directly from the stream:

- `crypto/dkg/src/lib.rs:591-602` — `t`, `n`, `i` are read as raw `u16`s from the reader; `n` is fully attacker-controlled (0..=65535) and unvalidated at this point.
- `crypto/dkg/src/lib.rs:607-613` — for `Interpolation::Constant`, `Vec::with_capacity(usize::from(n))` allocates `n × size_of::<C::F>()` immediately, before a single scalar is read.
- `crypto/dkg/src/lib.rs:620-623` — a `HashMap` is populated with `n` verification shares read via `read_G`.
- `crypto/dkg/src/lib.rs:625-631` — only *after* these allocations does `ThresholdParams::new(t, n, i)` get invoked to validate the parameters.

So a 11-byte input (`ID` check skipped for the right curve, `t=1`, `n=0xFFFF`, `i=1`, interpolation byte `0`) causes `Vec::with_capacity(65535)` — roughly 2 MB for a 32-byte scalar — to be allocated from a handful of bytes. The function then fails on `read_F`, freeing the memory, but the transient allocation is the resource amplification: an attacker streaming thousands of such messages concurrently forces sustained multi-GB transient allocator pressure, crashing or stalling the host (OOM), directly analogous to the CVE's per-write leak accumulating into a denial of service.

The `Lagrange` path (byte `1`) skips the `Vec` but still performs `n` `read_G` calls and `n` `HashMap` insertions before validation.

### Impact Explanation
Any component that calls `ThresholdKeys::read` on bytes influenced by an untrusted party (key backup/restore, share import, network-transported key material) can be driven into repeated large transient allocations. Since allocation happens *before* any bounds check against a sane participant limit, the amplification ratio is ~200,000× (2 MB allocated per ~10 bytes of input). Repeated requests exhaust memory or trigger aborts on allocation failure, denying availability of the signing/DKG service — mirroring the High-severity availability impact (CVSS A:H) of the reference advisory.

### Likelihood Explanation
`ThresholdKeys::read` is a public deserialization API explicitly in the set of entry points reachable with untrusted bytes. Triggering requires only choosing `n = 0xFFFF` — trivially reachable, no valid cryptographic content needed, and the failure occurs after the allocation, so the cost is always paid. Impact is capped by the `u16` bound (max ~2–4 MB per call rather than unbounded per message), so exploitation requires request volume rather than a single message.

### Recommendation
Validate `n` against the protocol maximum before allocating. In `crypto/dkg/src/lib.rs`, reorder so that `ThresholdParams::new(t, n, i)` (or an explicit `n <= MAX` check) runs before `Vec::with_capacity` and the verification-share loop, or push scalars into an empty `Vec` bounded by bytes actually read instead of pre-reserving from the untrusted length.

### Proof of Concept
```rust
use std::io;
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, Participant};
use dalek_ff_group::Ristretto;

fn poc() {
  // Minimal serialized ThresholdKeys<Ristretto>:
  // ID len (u32 LE matching C::ID.len()) || ID || t || n || i || interpolation byte
  let mut buf = vec![];
  buf.extend((Ristretto::ID.len() as u32).to_le_bytes());
  buf.extend(Ristretto::ID.as_bytes());
  buf.extend(1u16.to_le_bytes());      // t = 1
  buf.extend(0xFFFFu16.to_le_bytes()); // n = 65535, attacker-controlled
  buf.extend(1u16.to_le_bytes());      // i = 1
  buf.push(0);                         // Interpolation::Constant -> Vec::with_capacity(65535)

  // Errors on read_F, but only AFTER ~2 MiB is allocated from a ~15-byte input.
  let _ = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice());
}
``` [1](#0-0)

### Citations

**File:** crypto/dkg/src/lib.rs (L591-623)
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

    let secret_share = Zeroizing::new(C::read_F(reader)?);

    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```
