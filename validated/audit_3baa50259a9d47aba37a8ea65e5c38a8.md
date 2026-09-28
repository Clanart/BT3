The analog maps cleanly: like the vault assuming the requested `amount` was the amount actually withdrawn, `Scanner` reports outputs as received funds regardless of whether they are actually spendable — concretely, `scan_block` feeds the coinbase transaction through `scan_transaction`, so outputs paying to a registered script inside a coinbase are returned as `ReceivedOutput`s even though they are immature and cannot be spent for 100 blocks.

### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s — funds accounted as received that cannot be spent - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`Scanner::scan_block` iterates over every transaction in `block.txdata`, including `txdata[0]` (the coinbase), and hands each to `scan_transaction`, which emits a `ReceivedOutput` for any output whose `script_pubkey` matches a registered script. Coinbase outputs are encumbered by a 100-block maturity rule at the consensus layer: they exist and match the scanner's key, but any transaction spending them is invalid until maturity. The scanner performs no coinbase/maturity discrimination, so the "received" set does not equal the "spendable" set — the same accounting-mismatch class as the Aave report (assumed value != actual usable value).

### Finding Description [1](#0-0)  shows `scan_block` calling `scan_transaction` on all `block.txdata` with no carve-out for the coinbase. [2](#0-1)  shows `scan_transaction` matching purely on `output.script_pubkey` and emitting `ReceivedOutput { offset, output, outpoint }` with no maturity or coinbase flag. A `ReceivedOutput`'s only metadata is the offset, the `TxOut`, and the outpoint [3](#0-2)  — nothing distinguishes an immature coinbase output, and `SignableTransaction::new` will happily consume it as an input [4](#0-3) , producing a transaction the network rejects.

### Impact Explanation
Any miner (an unprivileged party relative to Serai — mining a block that pays an output to the Serai multisig's P2TR script requires only including one extra coinbase output) can cause the scanner to report funds as received that are unspendable for 100 blocks. Downstream accounting treats `ReceivedOutput`s uniformly: the balance is inflated and the output is eligible for selection in `SignableTransaction::new`. Spending it produces a consensus-invalid transaction, causing failed spends, or requiring the integrator to maintain out-of-band maturity tracking the type itself does not provide. The "received but not spendable" mismatch also enables griefing during rotations where scanned balance drives decisions.

### Likelihood Explanation
Low-to-moderate: it requires a miner to deliberately add an output to the coinbase transaction paying Serai's script — cheap but requires mining a block. However, it can also occur non-adversarially (e.g., mining-pool payouts or any coinbase that happens to pay the scanned script). The doc comment [5](#0-4)  acknowledges the hazard, but the library still returns the misleading `ReceivedOutput` rather than filtering, leaving every consumer to independently implement the fix.

### Recommendation
In `scan_block`, either skip `block.txdata[0]` entirely or mark coinbase-derived outputs so callers can distinguish maturity: e.g., check `tx.is_coinbase()` inside `scan_transaction` callers and exclude or annotate. Alternatively, add a `mature: bool` / `coinbase` flag on `ReceivedOutput` (threaded through `read`/`write`) and have `SignableTransaction::new` reject immature inputs.

### Proof of Concept
```rust
// Conceptual: a block whose coinbase pays to the multisig script
let scanner = Scanner::new(group_key).unwrap();
let mut coinbase = Transaction { /* coinbase tx */ .. };
coinbase.output.push(TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: p2tr_script_buf(group_key).unwrap(),
});
let block = Block { header, txdata: vec![coinbase, /* ... */] };

let received = scanner.scan_block(&block);
assert_eq!(received.len(), 1); // reported as received

// But any SignableTransaction consuming received[0] within the next
// 100 blocks produces a consensus-invalid transaction.
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L89-97)
```rust
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
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

**File:** networks/bitcoin/src/wallet/mod.rs (L216-220)
```rust
  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
```

**File:** networks/bitcoin/src/wallet/mod.rs (L221-227)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-185)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```
