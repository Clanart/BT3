### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to M-05 (a mitigation that counted soon-to-be-unlockable balance as immediately usable), `Scanner::scan_block` scans `block.txdata[0]` — the coinbase transaction — and returns its outputs as `ReceivedOutput`s even though coinbase outputs are consensus-locked for 100 blocks. The struct is documented as "A spendable output" (`mod.rs:88-97`), yet the returned value is provably not spendable: any transaction built from it is rejected by Bitcoin consensus until maturity.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `scan_block` iterates over all transactions including the coinbase: [1](#0-0) . `scan_transaction` has no coinbase check and emits a `ReceivedOutput` purely by matching `script_pubkey` [2](#0-1) . `ReceivedOutput` carries only `offset`, `output`, and `outpoint` — no coinbase/maturity flag — so downstream code cannot distinguish an immature output from a spendable one [3](#0-2) . The doc comment at `mod.rs:218-220` admits the caveat but delegates filtering to the caller; the API itself still returns the locked funds as ordinary received outputs. The Serai processor works around this by slicing `block.txdata[1 ..]` [4](#0-3) , but the in-scope `bitcoin-serai` wallet API exposes the flaw to any consumer that calls `scan_block` directly — the exact edge-case the M-05 mitigation missed: balance that is merely *expected to become* available is treated as available now.

### Impact Explanation
An output reported as received by `scan_block` cannot be spent: spending an immature coinbase output violates Bitcoin consensus rules (100-block maturity), so any transaction built via `wallet/send.rs` from such a `ReceivedOutput` is invalid. This satisfies the "funds reported received that are not spendable" impact class — a consumer crediting scanned outputs could treat locked coinbase payments to a multisig script_pubkey as settled balance.

### Likelihood Explanation
Reachable by an unprivileged party: any miner can pay to the scanner's `script_pubkey` in a coinbase (the address is public), and any caller invoking `scan_block` on that block receives the output in the result set. The doc comment mitigates intent but not behavior; there is no programmatic guard.

### Recommendation
Skip the coinbase inside `scan_block` (iterate `block.txdata[1 ..]` or check `tx.is_coinbase()`), or mark `ReceivedOutput` with a maturity flag so callers must explicitly acknowledge the lock — mirroring the fix already applied manually in `processor/src/networks/bitcoin.rs`.

### Proof of Concept
```rust
// Regtest: mine a block paying the multisig's p2tr script in the coinbase
let block = rpc.get_block(&rpc.get_block_hash(height).await?).await?;
let outputs = scanner.scan_block(&block);
// outputs includes the coinbase output as a "spendable" ReceivedOutput,
// yet any tx consuming outputs[0].outpoint() fails consensus (immature coinbase)
assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0));
```
This is demonstrated by the existing test `networks/bitcoin/tests/wallet.rs:65-69`, which confirms `scan_block` returns the coinbase output identically to `scan_transaction(&block.txdata[0])` — the test only avoids the issue by mining 100 blocks before asserting spendability.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
```rust
/// A spendable output.
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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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

**File:** processor/src/networks/bitcoin.rs (L686-692)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
```
