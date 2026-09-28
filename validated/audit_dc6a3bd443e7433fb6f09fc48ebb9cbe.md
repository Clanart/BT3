### Title
Untrusted length in `ReceivedOutput::read` causes unbounded memory allocation via `TxOut` consensus decoding - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` deserializes a `TxOut` using rust-bitcoin's `consensus_decode`. The `TxOut`'s `script_pubkey` is a `Vec<u8>`/`ScriptBuf` prefixed by a CompactSize length controlled entirely by the input bytes. rust-bitcoin's `Decodable` implementation for byte vectors allocates `vec![0; len]` up front, so a crafted input declaring a ~4 GiB script causes a ~4 GiB heap allocation before a single byte of the payload is validated as present. An unprivileged party feeding bytes to `ReceivedOutput::read` can exhaust process memory — the Serai analog of the resource-quota bypass in CVE-2017-4969.

### Finding Description
In `ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134):

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      ...
    }
```

`TxOut::consensus_decode` reads a u64 `value` followed by `script_pubkey`, whose CompactSize length can declare up to ~4 GiB. rust-bitcoin allocates a buffer of the claimed length before attempting to fill it, unlike the chunked-read defensive pattern the codebase itself uses elsewhere (e.g., `Call::read` in `networks/ethereum/src/machine.rs:51-60`, which explicitly comments "A valid DoS would be to claim a 4 GB data is present" and reads in 1 KB chunks). No equivalent mitigation exists here, and the `BufReader::with_capacity(0, r)` wrapper provides no length limiting.

### Impact Explanation
A single small crafted input (on the order of ~45 bytes) triggers a multi-gigabyte allocation. Repeated submissions cause memory exhaustion and abort/OOM-kill of any process deserializing untrusted `ReceivedOutput`s (e.g., scanner/processor ingest paths), denying availability. This mirrors the CVE's availability impact (CVSS A:H) in Serai's own code.

### Likelihood Explanation
Any endpoint or integration that calls `ReceivedOutput::read` on bytes influenced by an external party (scanner results, queue messages, RPC payloads) is reachable by an unauthenticated/unprivileged submitter. The cost to the attacker is a handful of bytes; the cost to the victim is gigabytes of allocation per attempt.

### Recommendation
Bound `TxOut` decoding: wrap the reader in `r.take(MAX_SCRIPT_SIZE)` (or a fixed cap on the whole `ReceivedOutput`), or read the CompactSize length manually and reject lengths above a sane bound (e.g., standardness max script size ~10 KB, or an absolute consensus bound) before decoding. Alternatively, apply the same chunked-read pattern used in `ethereum::machine::Call::read`.

### Proof of Concept
```rust
use bitcoin_serai::wallet::ReceivedOutput;

// Secp256k1 scalar (32 bytes, e.g. 0x01..) + TxOut{ value: u64, script: CompactSize(huge) }
let mut buf = vec![1u8; 32];              // offset scalar
buf.extend(&0u64.to_le_bytes());          // TxOut.value = 0
buf.push(0xFF);                           // CompactSize tag: u64 follows
buf.extend(&4_000_000_000u64.to_le_bytes()); // script_len ~4 GB
// no actual script bytes needed; allocation happens before reads fail
let _ = ReceivedOutput::read(&mut buf.as_slice()); // allocates ~4 GB then errors
```