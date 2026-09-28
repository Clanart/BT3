### Title
`Scanner::scan_block` returns immature coinbase outputs as spendable received funds (insecure default) - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over `block.txdata` including `txdata[0]`, the coinbase transaction, and reports any outputs paying to a registered script as `ReceivedOutput`s indistinguishable from ordinary, immediately spendable payments. Bitcoin coinbase outputs are encumbered by a 100-block maturity rule (and are invalidated entirely if the block is reorged). This mirrors CVE-2023-49721's bug class: a permissive/insecure default left enabled that lets an external party bypass a security invariant — here, the invariant that a scanned output is a spendable input.

### Impact Explanation
`scan_transaction` matches purely on `output.script_pubkey` and constructs a `ReceivedOutput` carrying the offset, `TxOut`, and `OutPoint` (`networks/bitcoin/src/wallet/mod.rs:199-214`). `scan_block` feeds the coinbase through the same path (`mod.rs:221-227`). A `ReceivedOutput` produced this way flows into the send path as a spendable input, yet the transaction cannot be spent until 100 confirmations and disappears entirely on a reorg. Result: funds reported received that are not spendable — the exact acceptance criterion. The receiving system can be induced to credit a deposit that later vanishes (reorg) or to construct transactions that the network rejects (immature coinbase spend).

### Likelihood Explanation
Mining is permissionless: any unprivileged party who finds a block controls the coinbase outputs and can point one at Serai's scanned script (or a registered offset script) with zero cost beyond normal mining. Separately, natural coinbase payments (e.g., a pool payout to the deposit address) trigger the same path without any attacker. The docstring at `mod.rs:218-220` acknowledges the maturity problem, but the default API still returns the output rather than filtering or flagging it, so any caller that does not perform the documented post-processing pass is exposed — an insecure default.

### Recommendation
In `scan_block`, skip `block.txdata[0]` by default (or have `scan_transaction` reject transactions where `tx.is_coinbase()`), and expose the opt-in coinbase-inclusive behavior only via an explicitly named function whose return type distinguishes immature outputs, rather than relying on callers to perform a post-processing pass.

### Proof of Concept
1. An attacker mines a block whose coinbase (`txdata[0]`) pays `P` satoshis to `p2tr_script_buf(key)` — the exact script `Scanner::new` registers at `mod.rs:164`.
2. The downstream system calls `Scanner::scan_block(&block)`, which iterates all of `block.txdata` (`mod.rs:223`), and `scan_transaction` matches the coinbase output's `script_pubkey` at `mod.rs:205`.
3. `scan_block` returns a `ReceivedOutput { offset: 0, output, outpoint }` (`mod.rs:206-211`) identical in form to a normal confirmed payment.
4. Any attempt to spend it is rejected by the network until 100 blocks elapse, and a reorg of that block silently deletes the output — while the scanner had already reported it as received funds. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L162-166)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
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
