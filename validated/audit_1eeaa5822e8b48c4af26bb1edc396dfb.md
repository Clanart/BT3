### Title
Deposits received via coinbase transactions are never registered, permanently locking funds under the vault key - ([File: processor/src/networks/bitcoin.rs])

### Summary
`Bitcoin::get_outputs` iterates `block.txdata[1 ..]`, skipping `txdata[0]` (the coinbase) unconditionally — not just for the current block, but forever. Any output paying a Serai-registered script (external, branch, change, or forward address) inside a coinbase transaction is invisible to the scanner, so the funds are received on-chain yet never credited, never scheduled, and never spendable. This is the direct analog of "received tokens not registered in the accounting registry."

### Finding Description
The in-scope `Scanner`/`scan_block` API explicitly supports scanning coinbase outputs and documents that maturity is a *post-processing* concern:

`networks/bitcoin/src/wallet/mod.rs:216-227` — `scan_block` scans all of `block.txdata`, noting "If received outputs must be immediately spendable, a post-processing pass is needed to remove those outputs. Alternatively, scan_transaction can be called on `block.txdata[1 ..]`." [1](#0-0) 

The processor, however, hardcodes the skip with no delayed rescan:

`processor/src/networks/bitcoin.rs:686-700` — `get_outputs` does `for tx in &block.txdata[1 ..]` and pushes every `scanner.scan_transaction(tx)` match into the reported outputs. There is no mechanism that ever revisits `txdata[0]` after the 100-block maturity window; each block is scanned exactly once at height `block_being_scanned` in `processor/src/multisigs/scanner.rs:550-567`. [2](#0-1) [3](#0-2) 

Consequently, an output `coinbase_tx.output[v]` whose `script_pubkey` equals `p2tr_script_buf(key + offset*G)` for a registered offset produces no `ReceivedOutput`, no `ScannerEvent::Block`, no DB entry (`save_outputs` is only reached via the emitted event), and no schedulable input. The correct handling — deferring coinbase outputs until maturity — is documented in the wallet API but never implemented. [4](#0-3) 

### Impact Explanation
Funds sent to a vault-controlled address via a coinbase output are permanently uncredited and unspendable: the scheduler only builds transactions from outputs the scanner emitted, so the BTC remains locked under the group key forever while Serai's accounting reports nothing received. This mirrors the source finding — value actually received by the system is omitted from the accounting registry (here, the output set / TVL rather than a token registry). Deposits are the canonical user-facing path (`OutputType::External`, offset `Scalar::ZERO`, registered in `Scanner::new` at `wallet/mod.rs:164`), so any miner — including a user paying out mining rewards directly to their Serai deposit address — triggers it with ordinary public transaction data. [5](#0-4) 

### Likelihood Explanation
Any coinbase transaction can pay an arbitrary script, and the four Serai address types are publicly derivable from the (public) group key. No privileged position beyond mining a block is required — and a user simply pointing their mining payout at their Serai deposit address hits this without malicious intent. The window is every block's `txdata[0]`, permanently.

### Recommendation
Do not permanently drop `txdata[0]`. Either scan it and filter outputs younger than 100 confirmations at spend time, or record matched coinbase outputs and emit them once mature (e.g., re-scan `txdata[0]` of block `n` when processing block `n + 100`). The wallet-layer API already contemplates exactly this post-processing pass.

### Proof of Concept
1. A Serai key `K` is registered; `Scanner::new(K)` maps `p2tr_script_buf(K)` → offset `0` (`External` kind) (`wallet/mod.rs:162-166`, `processor/src/networks/bitcoin.rs:318-322`).
2. A miner (or a user directing pool payouts) creates a coinbase whose output pays `p2tr_script_buf(K)` with value ≥ `N::DUST`, and it confirms in block `B`.
3. `get_outputs(&B, K)` iterates `B.txdata[1 ..]`; the coinbase output is never matched, so `outputs` stays empty and no `ScannerEvent::Block` is emitted (`processor/src/networks/bitcoin.rs:691`, `scanner.rs:697`).
4. Block `B` is marked scanned; no subsequent scan revisits it. After 100 blocks the output is mature and spendable on-chain, yet no processor knows it exists — the BTC is locked under `K` indefinitely and absent from all accounting.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L162-166)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
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

**File:** processor/src/multisigs/scanner.rs (L542-567)
```rust
        // Scan new blocks
        // TODO: This lock acquisition may be long-lived...
        let mut scanner_lock = scanner_hold.write().await;
        let scanner = scanner_lock.as_mut().unwrap();

        let mut has_activation = false;
        let mut outputs = vec![];
        let mut completion_block_numbers = vec![];
        for (activation_number, key) in scanner.keys.clone() {
          if activation_number > block_being_scanned {
            continue;
          }

          if activation_number == block_being_scanned {
            has_activation = true;
          }

          let key_vec = key.to_bytes().as_ref().to_vec();

          // TODO: These lines are the ones which will cause a really long-lived lock acquisition
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
          }
```

**File:** processor/src/multisigs/scanner.rs (L697-704)
```rust
        let sent_block = if has_activation || is_retirement_block || (!outputs.is_empty()) {
          // Save the outputs to disk
          let mut txn = db.txn();
          ScannerDb::<N, D>::save_outputs(&mut txn, &block_id, &outputs);
          txn.commit();

          // Send all outputs
          if !scanner.emit(ScannerEvent::Block { is_retirement_block, block: block_id, outputs }) {
```
