### Title
`Scanner::scan_block` reports maturity-burdened coinbase outputs as received funds - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`Scanner::scan_block` iterates over every transaction in a block, including `txdata[0]` (the coinbase), and returns any matching outputs as `ReceivedOutput`s with no maturity check. A miner can pay a Serai multisig address in a coinbase transaction, and the scanner will report those funds as received even though they are unspendable for 100 blocks — an analog of "reading an external feed without checking whether its answer is currently usable" (the sequencer-down class).

### Finding Description
The reported bug class is consuming external data without a liveness/freshness gate, causing stale or invalid data to be treated as valid. In Serai's Bitcoin wallet code, `Scanner::scan_transaction` matches any output whose `script_pubkey` is in `self.scripts` and constructs a `ReceivedOutput` with the txid/vout, performing no check on what kind of transaction produced the output [1](#0-0) . `scan_block` then feeds *all* transactions, including the coinbase `block.txdata[0]`, through `scan_transaction` [2](#0-1) .

Bitcoin consensus makes coinbase outputs unspendable until they are 100 blocks deep. The code itself acknowledges this only in a doc comment: "This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed" [3](#0-2) . The returned `ReceivedOutput` carries no flag distinguishing it from a normal spendable output, so any caller that does not perform the manual filtering described in the comment will treat immature block-subsidy funds as available inputs.

Notably, the processor-side Bitcoin network code recognizes the hazard and explicitly skips the coinbase: `// Skip the coinbase transaction which is burdened by maturity` before iterating `block.txdata[1 ..]` [4](#0-3) . The standalone `Scanner` in `networks/bitcoin/src/wallet/mod.rs` applies no equivalent guard, so the two code paths disagree about what constitutes a "received" output.

### Impact Explanation
A `ReceivedOutput` produced from a coinbase transaction is reported as funds received by the multisig but cannot be included as an input to a spending transaction for 100 blocks. If the wallet/scanner output is consumed directly (scheduler planning, balance accounting, any library user other than `processor::networks::bitcoin`), it yields "funds reported received that are not spendable": plans constructed over the immature outpoint produce transactions that the network rejects, and accounting treats unspendable value as spendable. This is exactly the failure mode of the source finding — an answer from an external source (here, the block's outputs) is consumed without checking a domain-mandatory precondition (coinbase maturity, analogous to the sequencer-uptime flag).

### Likelihood Explanation
Any miner can create a coinbase paying a Serai-derived P2TR script; no collusion, key material, or privileged position is required beyond producing one block. The condition also occurs naturally whenever a Serai address is used as a mining payout address. Whether it manifests depends on the caller skipping the manual post-processing the doc comment mandates — the in-repo processor does filter, but the library function itself is the defect site and silently returns the bad output.

### Recommendation
Have `scan_block` exclude `block.txdata[0]` by default (matching the processor's `get_outputs` behavior), or return a flag/`is_coinbase` marker on `ReceivedOutput` so maturity status cannot be silently dropped. If coinbase scanning must remain supported, gate it behind an explicit `scan_coinbase` API so the immature case is opt-in rather than the default behavior of the general-purpose scan function.

### Proof of Concept
1. Register a `Scanner` for key `K` via `Scanner::new(K)` (`p2tr_script_buf` is inserted into `self.scripts`).
2. A miner mines a block whose coinbase `txdata[0]` pays to `p2tr_script_buf(K)` — e.g. on regtest, `generatetoaddress` to the P2TR address (the existing test does exactly this and asserts `scan_block` returns the coinbase output equal to `scan_transaction(&block.txdata[0])` [5](#0-4) ).
3. `scan_block(&block)` returns a `ReceivedOutput` for `(coinbase_txid, 0)` indistinguishable from a normal payment.
4. Any attempt to build/sign a transaction spending that `ReceivedOutput` within the next 100 blocks produces a transaction rejected by Bitcoin consensus (`bad-txns-premature-spend-of-coinbase`), while the wallet layer had reported the funds as received with no indication of the restriction.

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

**File:** processor/src/networks/bitcoin.rs (L689-692)
```rust
    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
```

**File:** networks/bitcoin/tests/wallet.rs (L63-70)
```rust
  let block = rpc.get_block(&rpc.get_block_hash(block_number).await.unwrap()).await.unwrap();

  let mut outputs = scanner.scan_block(&block);
  assert_eq!(outputs, scanner.scan_transaction(&block.txdata[0]));

  assert_eq!(outputs.len(), 1);
  assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0));
  assert_eq!(outputs[0].value(), block.txdata[0].output[0].value.to_sat());
```
