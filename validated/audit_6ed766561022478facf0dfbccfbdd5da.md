### Title
Unbounded memory allocation via attacker-controlled `CompactSize` length in `TxOut` decoding inside `ReceivedOutput::read` - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes a Bitcoin `TxOut` and `OutPoint` using rust-bitcoin's `consensus_decode` directly on untrusted reader bytes [1](#0-0) . `TxOut::consensus_decode` reads a `CompactSize` varint declaring the `script_pubkey` length, and rust-bitcoin's `Vec<u8>` decoding performs a `vec![0; len]`-style allocation for the full declared length before reading a single payload byte. A crafted input can therefore declare a multi-gigabyte length with only ~10 bytes of input, forcing a huge allocation and aborting the process (OOM) — a denial of service analogous to the Squid message-processing crash (CVE-2023-49285), where malformed message bytes cause a fatal overread. Here malformed serialized bytes cause a fatal over-allocation.

### Finding Description
`ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs) reads:

1. A 32-byte scalar offset via `Secp256k1::read_F` (bounded).
2. A `TxOut` via `TxOut::consensus_decode(&mut buf_r)` — this decodes `Amount` (8 bytes) then `ScriptBuf`, whose consensus decoding reads a `CompactSize` varint and allocates a buffer of that exact declared size before calling `read_exact`. There is no bound check: a declared length of up to `u64::MAX` (or practically 4 GB) causes an immediate allocation of that size.
3. An `OutPoint` similarly via `consensus_decode`.

The vulnerable code:

```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
}
```

`BufReader::with_capacity(0, r)` passes the raw attacker-controlled stream straight into the consensus decoder — no framing, no length cap, no chunked read. Contrast with `coordinator/src/tributary/transaction.rs`, which explicitly bounds `commitments_len * each_commitments_len` against `TRANSACTION_SIZE_LIMIT` [2](#0-1) , and `networks/ethereum/src/machine.rs` `Call::read`, which reads claimed lengths in 1 KB chunks precisely to avoid "a valid DoS ... to claim a 4 GB data is present for only 4 bytes" [3](#0-2) . The Bitcoin wallet path lacks the mitigation the codebase itself documents as necessary.

`ReceivedOutput::read` is reached whenever a serialized `Plan`/`Output` is deserialized: `Plan::<N>::read` loops `N::Output::read(reader)` for `u32`-counted inputs [4](#0-3) , and the Bitcoin processor's output type is `ReceivedOutput` (see `processor/src/networks/bitcoin.rs`). Any flow where serialized plan/output bytes originate outside the local node (coordinator-produced plans, peer-delivered serialized outputs) funnels attacker bytes into this decoder.

### Impact Explanation
A single short malicious byte string (`<scalar> <8-byte amount> <CompactSize len = 0xFFFFFFFF>` — roughly 45 bytes total) causes an attempt to allocate ~4 GiB. On allocation failure Rust aborts (allocation failure is not recoverable via `io::Result`), killing the process performing deserialization — i.e., the signing/scanning processor handling Bitcoin multisig duties. This is a remote, unauthenticated denial of service against threshold signing availability, matching the CVE-2023-49285 impact class (DoS in untrusted message processing). If the allocation succeeds, repeated requests can still exhaust memory and degrade/crash the node.

### Likelihood Explanation
Reachability depends on serialized `ReceivedOutput`/`Plan` bytes crossing a trust boundary into the node. The in-scope rules explicitly designate untrusted bytes fed to `ReceivedOutput::read` as a valid attack surface. Any node or relay path that forwards serialized plans/outputs without an outer size bound (the `io::Read` interface has no inherent limit) exposes this. The exploit requires only a few crafted bytes and no valid keys, signatures, or consensus position — likelihood is high wherever the read path accepts externally sourced bytes.

### Recommendation
Bound the `TxOut`/`OutPoint` decode. Concretely:
- Wrap the reader in `io::Read::take(MAX_TXOUT_SIZE)` (e.g., `ScriptBuf` is at most ~10 KB for standard outputs; a cap of a few hundred bytes after the amount suffices for consensus-decoded `TxOut`), so `consensus_decode` hits EOF instead of honoring the declared length, and/or
- Pre-read the `CompactSize` varint manually, reject lengths above a sane maximum before any allocation, and only then call `consensus_decode` on a length-limited reader, mirroring the chunked-read approach used in `networks/ethereum/src/machine.rs` `Call::read`.

### Proof of Concept
```rust
// Conceptual PoC against ReceivedOutput::read
// Bytes: 32-byte scalar || TxOut(value=0, script_pubkey len = 0xFFFFFFFF)
let mut malicious = Vec::new();
malicious.extend([1u8; 32]);            // canonical scalar offset (non-zero, < order)
malicious.extend(0u64.to_le_bytes());   // TxOut.value = 0 sats
// CompactSize encoding of 0xFFFFFFFF
malicious.push(0xFF);
malicious.extend(0xFFFFFFFFu64.to_le_bytes());
// No payload bytes follow.

// ReceivedOutput::read(&mut malicious.as_slice()) will:
//  - read the scalar fine
//  - TxOut::consensus_decode sees VarInt len = 0xFFFFFFFF
//  - attempts to allocate a ~4 GiB Vec<u8> -> OOM abort / DoS
```
No funds, keys, or protocol participation are required; the crash occurs purely in deserialization before any validation.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }
```

**File:** coordinator/src/tributary/transaction.rs (L283-296)
```rust
          let mut each_commitments_len = [0; 2];
          reader.read_exact(&mut each_commitments_len)?;
          let each_commitments_len = usize::from(u16::from_le_bytes(each_commitments_len));
          if (commitments_len * each_commitments_len) > TRANSACTION_SIZE_LIMIT {
            Err(io::Error::other(
              "commitments present in transaction exceeded transaction size limit",
            ))?;
          }
          let mut commitments = vec![vec![]; commitments_len];
          for commitments in &mut commitments {
            *commitments = vec![0; each_commitments_len];
            reader.read_exact(commitments)?;
          }
          commitments
```

**File:** networks/ethereum/src/machine.rs (L51-60)
```rust
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

**File:** processor/src/plan.rs (L183-188)
```rust
    let mut inputs = vec![];
    let mut buf = [0; 4];
    reader.read_exact(&mut buf)?;
    for _ in 0 .. u32::from_le_bytes(buf) {
      inputs.push(N::Output::read(reader)?);
    }
```
