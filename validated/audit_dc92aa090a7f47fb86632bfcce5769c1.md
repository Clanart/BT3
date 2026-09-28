### Title
Untrusted `ReceivedOutput` can claim an unspendable outpoint - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` accepts the spending `offset`, the claimed `TxOut`, and the claimed `OutPoint` as independent serialized fields and performs no consistency or authorization check tying the outpoint to an output actually created by the referenced Bitcoin transaction.

### Finding Description
`ReceivedOutput::read` parses `offset`, `output`, and `outpoint` independently and returns them as a spendable output without checking that `outpoint` resolves to `output` on-chain. `Scanner::scan_transaction` normally establishes this relationship by deriving the outpoint from the containing transaction and copying the matching transaction output. A forged serialized `ReceivedOutput` can retain a valid scanner-controlled script and value while replacing the outpoint with a nonexistent output or an output containing a different script/value.

### Impact Explanation
A caller that accepts serialized `ReceivedOutput` values can report or queue funds as received even though the claimed outpoint is not spendable by the referenced Bitcoin transaction. Depending on wallet flow, this can cause incorrect balances or construction of a spend transaction around a nonexistent/mismatched input.

### Likelihood Explanation
The object is directly deserializable from untrusted bytes through the public `ReceivedOutput::read` API. The decoder verifies only encodings, not the semantic binding between the transaction output and its claimed outpoint.

### Recommendation
Treat `ReceivedOutput` as authenticated internal state, or add a binding/verification step before spending. Consumers receiving untrusted bytes should re-derive the output from the referenced transaction and compare the full `TxOut` before accepting it. If untrusted persistence is supported, include an authenticated transaction identifier or rescan the transaction before use.

### Proof of Concept
1. Scan a transaction and obtain a genuine `ReceivedOutput` for a registered key.
2. Serialize it with `ReceivedOutput::serialize`.
3. Replace only the encoded `outpoint` field with a nonexistent transaction ID/vout while retaining the original `offset` and `TxOut`.
4. Deserialize with `ReceivedOutput::read`.
5. The result is accepted and reports the original value, despite the referenced outpoint not containing that output.

Relevant code:

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

The trusted construction path contrasts with this by computing the outpoint from the transaction being scanned:

```rust
// networks/bitcoin/src/wallet/mod.rs
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput {
    offset: *offset,
    output: output.clone(),
    outpoint: OutPoint::new(tx.compute_txid(), vout),
  });
}
```