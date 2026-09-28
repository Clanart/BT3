### Title
Scanner::scan_block Reports Immature Coinbase Outputs as Spendable Received Funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over `block.txdata` including `txdata[0]`, the coinbase transaction. Coinbase outputs are bound by Bitcoin's 100-block maturity rule — a height-based validity precondition analogous to `valid_till_block_height`, evaluated in the opposite direction (a minimum height rather than a maximum). The scanner performs no maturity check and emits a `ReceivedOutput` indistinguishable from a spendable one. Any consumer that feeds `scan_block` results into `SignableTransaction::new` constructs a transaction that the Bitcoin network will reject as non-standard/invalid (`bad-txns-premature-spend-of-coinbase`), because the library models the output as immediately spendable.

### Finding Description
`scan_transaction` matches `output.script_pubkey` against registered scripts and pushes a `ReceivedOutput { offset, output, outpoint }` with no field recording the containing transaction's position or the block height [1](#0-0) . `scan_block` then blindly extends results over every transaction in the block, including `txdata[0]` [2](#0-1) . The only mitigation is a doc comment stating "a post-processing pass is needed," and the sole caller that is safe is `processor/src/networks/bitcoin.rs` `get_outputs`, which manually slices `block.txdata[1 ..]` [3](#0-2) . The public API itself accepts the unvalidated "has this output matured?" parameter implicitly (block position is never supplied nor checked), identical in shape to the FastBridge report: a validity bound exists in the protocol but is never enforced on the input.

Additionally, `ReceivedOutput::read` reconstructs `offset`, `output`, and `outpoint` from untrusted bytes with only canonical-scalar and consensus-decode checks — it never verifies the `offset` actually corresponds to `output.script_pubkey` (i.e., that `script_pubkey == p2tr(key + G*offset)`), so a deserialized output can carry an offset that does not match the script it claims to spend [4](#0-3) .

### Impact Explanation
A miner (or any party able to influence a coinbase transaction, which on a chain where Serai scans is reachable by an unprivileged party crafting a Bitcoin transaction) can pay a Serai-registered `p2tr` script in the coinbase. `scan_block` reports it as a normal `ReceivedOutput`. Downstream, that output is indistinguishable from a spendable UTXO: fee/change math in `SignableTransaction` will include it, and the resulting spend is rejected by the network for 100 blocks — a batch that can never confirm, or which (if later spent post-maturity) represented funds that were never actually available at scan time. This is the "funds reported received that are not spendable" outcome.

### Likelihood Explanation
Any miner can target a known Serai deposit/forwarded address in a coinbase output at negligible cost. Exploitation requires a consumer calling `scan_block` rather than `scan_transaction` over `txdata[1 ..]`; the in-repo processor does the latter, which lowers practical likelihood to Medium — but the vulnerable primitive is the public in-scope API and the failure mode is silent (no error, no maturity flag on `ReceivedOutput`).

### Recommendation
- Change `scan_block` to skip `block.txdata[0]` by default, or return a maturity/position flag on `ReceivedOutput` so consumers cannot mistake a coinbase output for a spendable one.
- In `ReceivedOutput::read` (or a separate `verify` method), when the scanning key is available, recompute `p2tr_script_buf(key + G*offset)` and reject the deserialization if it does not equal `output.script_pubkey`.

### Proof of Concept
```rust
// networks/bitcoin context
let scanner = Scanner::new(key).unwrap();
// A block whose coinbase pays key's p2tr script:
//   block.txdata[0].output[0].script_pubkey == p2tr_script_buf(key)
let outputs = scanner.scan_block(&block);
// outputs[0] is a ReceivedOutput with offset ZERO and a real outpoint,
// yet spending it before height+100 fails consensus coinbase maturity.
// Nothing in `outputs` distinguishes it from a mature payment.
```
The behavior is confirmed by the doc comment on `scan_block` itself and by the processor's compensating `txdata[1 ..]` slice [5](#0-4) [6](#0-5) .

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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L216-227)
```rust
  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```

**File:** processor/src/networks/bitcoin.rs (L686-692)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
```
