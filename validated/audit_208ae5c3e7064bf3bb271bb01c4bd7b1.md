### Title
Attacker-controlled `msg_len` drives a multi-gigabyte allocation in `MusigContext::read`, enabling remote OOM denial of service - (File: crypto/dkg/musig/src/lib.rs)

### Summary
`MusigContext::read` (the `musig` DKG sub-crate, in scope under `crypto/dkg`) deserializes an untrusted byte stream: it reads a `u32` message length and immediately materializes a `vec![0; len]` buffer before any bytes of the message are actually consumed. This is the same bug class as ALPINE-CVE-2017-11613 — in LibTIFF, `td_imagelength` flowed directly into `_TIFFCheckMalloc`/`ChopUpSingleUncompressedStrip`; here, an attacker-controlled `u32` length field flows directly into a Rust heap allocation of up to ~4 GiB per deserialization.

### Finding Description
In `crypto/dkg/musig/src/lib.rs`, `MusigContext::read` parses:
- `index: u32`, `multisig: u32`, then `msg_len: u32` (little-endian), followed by `let mut msg = vec![0; msg_len];` and `reader.read_exact(&mut msg)`.

The allocation is performed up front from the untrusted length word. A peer in a DKG/signing session (the `musig` module backs the `Musig` algorithm used with FROST `ThresholdKeys`/`Commitments`) can send a 12-byte message declaring `msg_len = 0xFFFF_FFFF` with no payload at all. Each such message forces a ~4 GiB allocation attempt; `read_exact` only fails *after* the allocation. The same shape exists in `ThresholdKeys::read` in `crypto/dkg/src/lib.rs:608` where an attacker-controlled `u16 n` seeds `Vec::with_capacity(usize::from(n))` for `Interpolation::Constant`, but the u16 bound caps that at ~2 MiB; the musig `u32` path is the reachable, high-severity instance. The codebase itself recognizes this class — `networks/ethereum/src/machine.rs:51-60` contains an explicit comment ("A valid DoS would be to claim a 4 GB data is present for only 4 bytes") and reads in 1 KB chunks, while `coordinator/src/p2p.rs:264` caps `len` against `MAX_LIBP2P_REQRES_MESSAGE_SIZE` before allocating. No such guard exists on the musig context length. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
An unprivileged participant (or any party able to deliver bytes to a validator's DKG/signing message ingestion path that calls `MusigContext::read`/the musig context reader) can crash or OOM-kill honest validators by submitting a ~12-byte payload. Repeated submissions deny service to the threshold-signing process entirely — a remote, unauthenticated-in-effect DoS proportional to `msg_len`, with zero bandwidth cost to the attacker (allocation precedes `read_exact` failure). This matches the CVE's "hang the system or trigger the OOM killer" impact.

### Likelihood Explanation
Any counterparty in a DKG or signing round can reach the reader with public input bytes; no signature, key share, or valid proof is needed because the allocation happens during deserialization, before verification. Severity is Medium: availability loss only, no confidentiality/integrity impact, but trivially repeatable and cheap.

### Recommendation
Cap `msg_len` before allocating (e.g., a `MAX_CONTEXT_MSG_LEN` consistent with what MusigContext legitimately carries), or read incrementally in bounded chunks as `Call::read` does. Apply the same pre-allocation bound check to the `u16`-driven `Vec::with_capacity(n)` in `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:608`) and any analogous length-prefixed reads in `crypto/dkg`.

### Proof of Concept
1. Craft bytes: `index (u32 LE) || multisig (u32 LE) || msg_len = 0xFFFF_FFFF (u32 LE)` — 12 bytes total, no payload.
2. Feed to `MusigContext::read` (or the musig context deserialization reached during PedPoP/MuSig session message handling in `crypto/dkg/musig`).
3. Observe a ~4 GiB `vec![0; len]` allocation attempt prior to `read_exact` returning an unexpected-EOF error; repeated submissions exhaust process memory and trigger the OOM killer on the validator.

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

**File:** networks/ethereum/src/machine.rs (L45-60)
```rust
    let mut data_len = {
      let mut data_len = [0; 4];
      reader.read_exact(&mut data_len)?;
      usize::try_from(u32::from_le_bytes(data_len)).expect("u32 couldn't fit within a usize")
    };

    // A valid DoS would be to claim a 4 GB data is present for only 4 bytes
    // We read this in 1 KB chunks to only read data actually present (with a max DoS of 1 KB)
    let mut data = vec![];
    while data_len > 0 {
      let chunk_len = data_len.min(1024);
      let mut chunk = vec![0; chunk_len];
      reader.read_exact(&mut chunk)?;
      data.extend(&chunk);
      data_len -= chunk_len;
    }
```

**File:** coordinator/src/p2p.rs (L261-271)
```rust
    let mut len = [0; 4];
    io.read_exact(&mut len).await?;
    let len = usize::try_from(u32::from_le_bytes(len)).expect("not at least a 32-bit platform?");
    if len > MAX_LIBP2P_REQRES_MESSAGE_SIZE {
      Err(io::Error::other("request length exceeded MAX_LIBP2P_REQRES_MESSAGE_SIZE"))?;
    }
    // This may be a non-trivial allocation easily causable
    // While we could chunk the read, meaning we only perform the allocation as bandwidth is used,
    // the max message size should be sufficiently sane
    let mut buf = vec![0; len];
    io.read_exact(&mut buf).await?;
```
