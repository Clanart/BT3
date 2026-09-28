### Title
Bitcoin `Scanner::scan_block` reports immature coinbase outputs as spendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over every transaction in a block, including `block.txdata[0]` (the coinbase). A coinbase output paying to a scanned script is returned as a `ReceivedOutput`, indistinguishable from a normal payment, even though Bitcoin consensus forbids spending coinbase outputs for 100 blocks (COINBASE_MATURITY). This is the same bug class as the GMX report: a value (here, an output) is accepted as valid/usable because only the surface check (script_pubkey match) is performed, while the temporal/liveness condition (maturity, analogous to sequencer uptime) is never verified.

### Finding Description
`scan_transaction` matches solely on `output.script_pubkey` membership in `self.scripts` and pushes a `ReceivedOutput` [1](#0-0) . `scan_block` feeds it all of `block.txdata`, with only a doc comment noting the caller "needs" a post-processing pass to drop coinbase outputs [2](#0-1) . Nothing in `ReceivedOutput`, `scan_transaction`, or `scan_block` records or checks whether the origin transaction is a coinbase. Downstream, a `ReceivedOutput` is treated as a spendable input: `SignableTransaction::new`/`TransactionSignMachine` will happily build a Taproot key-spend over it and the coordinator will sign it, producing a transaction the Bitcoin network rejects (bad-txns-coinbase). The processor's own `get_outputs` had to work around this by manually slicing `block.txdata[1 ..]` [3](#0-2) , proving the footgun is real — any other caller of `scan_block` (the documented block-scanning API) inherits the bug.

### Impact Explanation
An unprivileged party — any miner — can create a block whose coinbase pays to Serai's (or any watched) Taproot script. `scan_block` then reports funds as received that are not spendable for 100 blocks. If these outputs enter the scheduling/signing pipeline, Serai signs a transaction spending an immature coinbase, which is invalid under consensus: the "received" funds cannot actually be moved, and a Burn/spend plan built on them fails on-chain. This matches the accepted impact class "funds reported received that are not spendable", analogous to orders filled on prices that were valid-looking but stale.

### Likelihood Explanation
Low-to-moderate. It requires a miner to direct a coinbase output to the scanned key, which costs them the subsidy (they can recover it after maturity). The failure also depends on a consumer calling `scan_block` rather than re-implementing the coinbase skip. No cryptographic break is needed, and the triggering input (a Bitcoin block) is fully public/attacker-controlled.

### Recommendation
Track coinbaseness in `scan_block`: skip `block.txdata[0]` inside `scan_block`, or record maturity on `ReceivedOutput` (e.g. an `immature: bool` / earliest-spendable height field serialized with the output) so the scheduler can defer it. If coinbase scanning is intentionally supported, enforce the check structurally instead of relying on a doc comment — e.g. have `scan_block` take the block height and refuse outputs with fewer than 100 confirmations.

### Proof of Concept
```rust
// networks/bitcoin/tests/wallet.rs already demonstrates the primitive:
// `send_and_get_output` mines 1 block paying to `p2tr_script_buf(key)`,
// then mines 100 more blocks *solely because the coinbase is immature*;
// the scanned output is only spendable after that second step.
rpc.rpc_call::<Vec<String>>(
  "generatetoaddress",
  serde_json::json!([1, Address::from_script(&p2tr_script_buf(key).unwrap(), ...)]),
).await.unwrap();
// Without these 100 blocks, scan_block still returns the coinbase output
// as a ReceivedOutput with offset ZERO — reported received, not spendable:
let block = rpc.get_block(&rpc.get_block_hash(block_number).await.unwrap()).await.unwrap();
let outputs = scanner.scan_block(&block); // includes txdata[0], the immature coinbase
assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0));
```
The test's own comment "Mine until maturity" (networks/bitcoin/tests/wallet.rs:54-61) confirms the output would be unspendable if consumed immediately, yet `scan_block` surfaces it identically to a mature payment.

### Citations

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

**File:** processor/src/networks/bitcoin.rs (L686-700)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
        assert_eq!(output.tx_id(), tx.id());
        outputs.push(output);
      }
```
