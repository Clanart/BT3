### Title
`ThresholdKeys::read` pre-allocates a `Vec` sized by an attacker-controlled u16 `n` before any validation, enabling per-message memory exhaustion - (File: crypto/dkg/src/lib.rs)

### Summary

The Netty `Lz4FrameDecoder` bug class is: a small attacker-supplied header field (`decompressedLength`) is trusted to size an allocation before the payload is validated or even read. The analog in `dkg::ThresholdKeys::read` is the `n` (participant count) field: 2 bytes of untrusted input drive a `Vec::with_capacity(usize::from(n))` allocation of up to 65,535 field elements (~2 MB) plus a 65,535-iteration `HashMap`/`read_G` loop, all before `ThresholdParams::new` validates `n` against `t`/`i` at the end of the function.

### Finding Description

In `ThresholdKeys::read`, `n` is read from the wire as a raw u16 and immediately used to shape two allocations:

- `Interpolation::Constant` branch (kind byte `0`): `Vec::with_capacity(usize::from(n))` allocates `n * size_of::<C::F>()` (~32 B per scalar → ~2 MB for `n = 0xFFFF`), then loops `n` times on `C::read_F` [1](#0-0) 
- `verification_shares` is populated in a `for l in (1 ..= n)` loop of `read_G` calls [2](#0-1) 

Only after all reads complete is `ThresholdParams::new(t, n, i)` invoked, which is the first point `n` is semantically validated [3](#0-2) . There is no `MAX` bound on `n` anywhere in `ThresholdParams` — `new` only checks `t <= n` and `i <= n` [4](#0-3) , and `n` is genuinely a free u16 since legitimate multisigs may have large `n`.

This is the same structure as the Netty bug: the length field is trusted for sizing ahead of the bytes it describes. On a streaming/`io::Read` source (the stated threat model — untrusted bytes fed to `ThresholdKeys::read`), an attacker sends ~10 bytes (curve ID length + ID + `t` + `n = 0xFFFF` + `i` + kind byte `0`), then stalls. The ~2 MB `Vec` is allocated immediately and remains live while `read_exact` blocks on the withheld scalar bytes. Many concurrent stalled reads multiply the held memory with near-zero bandwidth cost (~10 bytes → ~2 MB is a ~200,000× amplification per stalled call).

### Impact Explanation

Memory exhaustion DoS. Each malicious "ThresholdKeys blob" forces a multi-megabyte allocation that is retained for as long as the attacker keeps the read blocked (dribbling bytes at an arbitrarily slow rate keeps it alive indefinitely). Thousands of concurrent partial messages pin gigabytes of heap with negligible attacker bandwidth, crashing or OOM-killing the process. Unlike proportional reads (e.g., `SchnorrAggregate::read` at `crypto/schnorr/src/aggregate.rs:77-88`, which grows a `Vec` only as points are actually parsed), the `with_capacity` call allocates up front regardless of whether the data ever arrives.

### Likelihood Explanation

Reachability: `ThresholdKeys::read` is an explicitly in-scope untrusted-byte sink. Any deployment where a peer-supplied or coordinator-supplied serialized key set is parsed from a stream (rather than a bounded buffer) exposes this. The attack requires no keys, no threshold position, and no valid proof — only the first ~10 bytes of well-formed framing. Amplification is capped at ~2 MB per message (u16 bound) rather than Netty's 32 MB, but it is trivially parallelizable, which is precisely the "many small requests stress memory" impact mode described in the advisory. Medium severity: bounded per-instance cost, unauthenticated reachability, availability-only impact.

### Recommendation

Do not allocate or loop from `n` until `n` is validated. Specifically, in `crypto/dkg/src/lib.rs::ThresholdKeys::read`:

- Reorder so `ThresholdParams::new(t, n, i)` is constructed and checked immediately after `t`, `n`, `i` are read, before the interpolation/verification-share loops.
- Impose a sane protocol cap on `n` (e.g., reject `n` exceeding the deployment's maximum participant count) before `Vec::with_capacity`.
- Prefer push-based growth (`Vec::new()` + `push` per successfully read element) over `with_capacity` for untrusted counts, matching the chunked-read mitigation already used elsewhere in Serai (e.g., the 1 KB chunked read in `networks/ethereum/src/machine.rs:51-60`).

### Proof of Concept

```rust
use std::io;
use ciphersuite::Ciphersuite;
use dkg::ThresholdKeys;
// e.g. ciphersuite for Ristretto or Secp256k1

fn dos_via_declared_n() {
    // Build a minimal malicious header:
    // [u32 ID_len][ID][u16 t][u16 n = 0xFFFF][u16 i = 1][kind = 0 (Constant)]
    let mut buf = Vec::new();
    buf.extend(&(C::ID.len() as u32).to_le_bytes());
    buf.extend(C::ID);
    buf.extend(&1u16.to_le_bytes());      // t
    buf.extend(&u16::MAX.to_le_bytes());  // n = 65535 -> ~2 MB Vec::with_capacity
    buf.extend(&1u16.to_le_bytes());      // i
    buf.push(0);                          // Interpolation::Constant

    // A reader that returns the header then blocks/errors slowly:
    // Vec of 65535 scalars is allocated before the first read_F fails on EOF.
    let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());
    // Observable: ~2 MB allocated and freed per call; against a streaming reader
    // that dribbles bytes, each concurrent call retains ~2 MB indefinitely.
}
```

Note on certainty: the allocation site and missing bounds check are directly confirmed in `crypto/dkg/src/lib.rs` lines 591-631. The reachability assumption relies on the engagement's declared sink list, which names `ThresholdKeys::read` as an entry point for untrusted bytes; the codebase itself does not show an in-scope caller feeding it network data (the libp2p request codec in `coordinator/src/p2p.rs:261-272` bounds messages at `MAX_LIBP2P_REQRES_MESSAGE_SIZE`, but that path is out of scope). Other in-scope readers (`Commitments::read`, `EncryptedMessage::read`, `SignatureShare::read`, `DLEqProof::read`) take their element counts from integrator-supplied `ThresholdParams`/generator lists or fixed-size field encodings, not from attacker bytes, so they do not exhibit this amplification pattern.

### Citations

**File:** crypto/dkg/src/lib.rs (L166-179)
```rust
  pub const fn new(t: u16, n: u16, i: Participant) -> Result<ThresholdParams, DkgError> {
    if (t == 0) || (n == 0) {
      return Err(DkgError::ZeroParameter { t, n });
    }

    if t > n {
      return Err(DkgError::InvalidThreshold { t, n });
    }
    if i.0 > n {
      return Err(DkgError::InvalidParticipant { n, participant: i });
    }

    Ok(ThresholdParams { t, n, i })
  }
```

**File:** crypto/dkg/src/lib.rs (L606-613)
```rust
    let interpolation = match interpolation[0] {
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
```

**File:** crypto/dkg/src/lib.rs (L620-623)
```rust
    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```

**File:** crypto/dkg/src/lib.rs (L625-631)
```rust
    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```
