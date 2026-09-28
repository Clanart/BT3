### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external bug class is "pushing funds to a destination that cannot use them, leaving them stuck." In bitcoin-serai, `Scanner::scan_block` iterates over every transaction in a block, including `txdata[0]` (the coinbase). A coinbase output paying to a registered scanner script is returned as a `ReceivedOutput`, indistinguishable from a normal payment, yet it is bound by Bitcoin's 100-block maturity rule and cannot be spent. Worse, the downstream consumer in `processor/src/networks/bitcoin.rs` deliberately skips `block.txdata[1 ..]`'s predecessor (the coinbase), so `scan_block` and `Bitcoin::get_outputs` disagree about whether the funds exist.

### Finding Description
`Scanner::scan_transaction` matches `output.script_pubkey` against registered scripts and pushes a `ReceivedOutput` for any match. `scan_block` calls it for all `block.txdata` entries, including the coinbase [1](#0-0) . The resulting `ReceivedOutput` exposes `offset`, `output`, and `outpoint` exactly like a mature, spendable UTXO [2](#0-1) . Any miner (an unprivileged party producing ordinary Bitcoin blocks) can put a P2TR output to the multisig's script in their coinbase. That output is consensus-unspendable for 100 blocks. Meanwhile `Bitcoin::get_outputs` explicitly skips the coinbase when reporting to the processor [3](#0-2) , so a user of the wallet-level `Scanner` sees funds as received that the network layer will never report and that cannot be signed/spent anyway.

### Impact Explanation
Funds are reported received that are not spendable. If `scan_block` output is trusted (e.g., credited to a depositor, used as a `SignableTransaction` input, or counted toward a balance), the credited funds are immature coinbase outputs: any spend attempt is consensus-invalid, and a reorg within the maturity window destroys them entirely, permanently sticking the accounting. This mirrors the blacklisted-recipient stuck-funds class: value is delivered to a state where it cannot be moved.

### Likelihood Explanation
Requires only a miner including a scanner-matching script in a coinbase — normal, permissionless block production. Exploitation further requires a consumer calling `scan_block` directly instead of `scan_transaction` on `block.txdata[1 ..]`; the API makes the buggy path the most natural one (the doc comment acknowledges maturity but still returns the outputs rather than filtering or flagging them).

### Recommendation
Have `scan_block` skip `block.txdata[0]` outright, or return coinbase-matching outputs in a separate "immature" classification so callers cannot confuse them with spendable `ReceivedOutput`s. Keep `scan_transaction` unchanged so callers that want full scanning can still opt in.

### Proof of Concept
1. Create `Scanner::new(key)` for an even-y key `key`.
2. As a miner, mine a block whose coinbase (`txdata[0]`) contains `TxOut { script_pubkey: p2tr_script_buf(key).unwrap(), value }`.
3. `scanner.scan_block(&block)` returns a `ReceivedOutput` with `outpoint = (coinbase_txid, vout)`, `offset = ZERO` [4](#0-3) .
4. Any attempt to spend it via `SignableTransaction::new(vec![output], ...)` produces a transaction rejected by consensus (immature coinbase), and `Bitcoin::get_outputs` never reports it [5](#0-4)  — the funds counted as received are unspendable/invisible downstream.

Note the doc comment at [6](#0-5)  discloses the maturity burden, which mitigates severity to Medium but does not change that the API returns non-spendable outputs as normal `ReceivedOutput`s.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L217-220)
```rust
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
