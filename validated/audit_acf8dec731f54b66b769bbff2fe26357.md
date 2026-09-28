### Title
Scanner reports outputs spent within the same block (and immature coinbase outputs) as received, crediting funds that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates every transaction in a block and reports any output whose `script_pubkey` matches a registered script as a `ReceivedOutput`, without checking whether that output is already spent by a later transaction in the same block, and without excluding coinbase outputs that are immature for 100 blocks. A received output is therefore persisted and treated as spendable wallet funds with no validity/expiry window — analogous to the Scroll issue where a persisted message can be replayed long after it ceased to reflect the user's intent.

### Finding Description
`Scanner::scan_transaction` matches only on `output.script_pubkey` and pushes a `ReceivedOutput` for every match [1](#0-0) . `scan_block` then calls it for every transaction in `block.txdata`, including `txdata[0]` (the coinbase) [2](#0-1) .

Two consequences:

1. **Same-block spend**: In Bitcoin, a transaction may spend an output created by an earlier transaction in the same block. An unprivileged party can craft `txA` paying to a Serai-scanned address and `txB` spending `txA`'s output in the same block. `scan_block` still emits the output as received. There is no cross-transaction UTXO check anywhere in the scanner.
2. **Coinbase maturity**: coinbase outputs matching a registered script (e.g., a miner pays the pool's scanned address as a coinbase output) are reported as received even though they are consensus-unspendable for 100 blocks. The doc comment acknowledges this but leaves enforcement to callers [3](#0-2) .

These `ReceivedOutput`s are exactly what `SignableTransaction::new` accepts as inputs, so downstream signing will attempt to spend a non-existent/immature UTXO, producing an invalid transaction — or worse, crediting a deposit that immediately left the wallet.

### Impact Explanation
Funds are reported received that are not spendable: an attacker can get a deposit credited for an output that is already consumed in the same block, and any attempted spend produces a transaction that nodes reject. For coinbase outputs, any `SignableTransaction` built over them is consensus-invalid for 100 blocks.

### Likelihood Explanation
Reachable by an unprivileged party sending ordinary Bitcoin transactions to a known scanned script (the external address is public). The same-block double-spend requires only two transactions ordered within one block, which miners routinely include.

### Recommendation
In `scan_block`, track the prevouts of each transaction and drop `ReceivedOutput`s whose `OutPoint` was consumed by a later transaction in the same block. Skip `block.txdata[0]` (or filter coinbase outputs) as the comment already suggests, enforcing it in the library rather than delegating to callers.

### Proof of Concept
1. Create `Scanner::new(key)`, get `p2tr_script_buf(key)`.
2. Broadcast `txA` with an output to that script, and `txB` spending `txA:vout0`, both mined in the same block.
3. `scanner.scan_block(&block)` returns a `ReceivedOutput` for `txA:vout0` even though the UTXO does not exist.
4. Building a `SignableTransaction` over it yields a transaction rejected by the network.

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
