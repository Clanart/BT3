### Title
Unbounded allocation / panic in `ReceivedOutput::read` via attacker-controlled `TxOut` script length - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The analog to CVE-2019-5010 (crafted input bytes causing a crash / denial of service in a parser reachable from untrusted data) exists in `ReceivedOutput::read`, which decodes a `bitcoin::TxOut` and `OutPoint` directly from the byte stream with `consensus_decode`. The `TxOut` consensus encoding begins with an 8-byte value followed by a compact-size-prefixed `script_pubkey` length. `consensus_decode` trusts that length prefix and allocates a `Vec` of the declared size before reading the body, so a serialized `ReceivedOutput` declaring a ~4 GiB script causes a huge allocation (OOM abort or `capacity_overflow` panic) even when the actual buffer is only a few dozen bytes long.

### Finding Description
`ReceivedOutput::read` reads a scalar offset and then hands the raw stream to rust-bitcoin's `Decodable` implementation without any size cap:

```rust
// networks/bitcoin/src/wallet/mod.rs
122|  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
123|    let offset = Secp256k1::read_F(r)?;
...
128|      output =
129|        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
130|      outpoint =
131|        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
```

`TxOut::consensus_decode` internally decodes `ScriptBuf`, which reads a `VarInt` length and pre-allocates `vec![0u8; len]` (up to `u32::MAX` ≈ 4 GiB, or larger via `Vec`-backed decoding paths) before attempting `read_exact`. The reader itself performs no pre-validation of the declared length against remaining input — unlike the bounds checks Serai applies elsewhere (e.g., `TRANSACTION_SIZE_LIMIT` in `Transaction::read`, `coordinator/src/tributary/transaction.rs:286`, or the `MAX_KEY_SHARES_PER_SET` check at line 431). The declared length therefore controls process memory allocation directly.

Additionally, the decoded `TxOut.value` is a raw `u64` taken on trust. Downstream, `SignableTransaction::new` sums these values with `sum::<u64>()` (`networks/bitcoin/src/wallet/send.rs:175`) and `SignableTransaction::fee` computes `prevouts_sum - outputs_sum` (`send.rs:139-141`) — both will panic on overflow/underflow under `overflow-checks` (debug builds) with crafted values.

### Impact Explanation
`ReceivedOutput::read` is one of the explicitly listed sinks for untrusted bytes: outputs are deserialized from data supplied by external parties (scanner outputs, coordinator messages, persisted/network-serialized outputs). An unprivileged party that can cause a node to deserialize a crafted `ReceivedOutput` can force a multi-gigabyte allocation or a panic, crashing the process — the same denial-of-service shape as the Python NULL-deref, mapped onto Serai's deserialization boundary. With `overflow-checks` enabled, the fee/`input_sat` arithmetic in `SignableTransaction` provides a second deterministic panic path on the same untrusted `value` field.

### Likelihood Explanation
Triggering requires only delivering attacker-controlled bytes to `ReceivedOutput::read` — no keys, no threshold participation, no valid signatures. A ~15-byte payload (`offset || value || varint_len=0xFFFFFFFF`) is sufficient. The rust-bitcoin `Vec` decoding allocates before validating that the declared bytes are actually present, so no real 4 GiB transfer is needed.

### Recommendation
Cap the declared `script_pubkey` length (and any length-prefixed field) before decoding — e.g., decode `TxOut` manually: read the `u64` value, read a bounded `VarInt`, reject lengths above a small constant (Taproot outputs are ≤ ~43 bytes), then read the script. Alternatively wrap the reader in `reader.take(MAX_TXOUT_SIZE)` before `consensus_decode`. Also use `checked_sum`/`checked_sub` for the `u64` fee and input-value arithmetic in `SignableTransaction` so malicious `value` fields return `TransactionError` instead of panicking.

### Proof of Concept
```rust
// Feeding this to ReceivedOutput::read triggers a ~4 GiB allocation:
let mut buf = vec![];
buf.extend([1u8; 32]);                        // offset scalar
buf.extend(0u64.to_le_bytes());               // TxOut.value
buf.extend(0xFFFF_FFFFu32.to_le_bytes());     // script len via VarInt (0xFD/0xFE form)
// plus a few bytes so varint decodes; read_exact fails only AFTER the alloc
let _ = ReceivedOutput::read(&mut buf.as_slice()); // aborts / OOMs
```

Confidence caveat: the exact allocation behavior lives inside rust-bitcoin's `Decodable` for `Vec<u8>`/`ScriptBuf` (external dependency, not indexed here); its standard implementation pre-allocates the declared length before reading, which is the basis for this finding.