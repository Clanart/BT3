### Title
Scanner reports immature coinbase outputs as spendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` scans every transaction in a block, including `txdata[0]` — the coinbase transaction — and returns matching outputs as `ReceivedOutput` without checking coinbase maturity. An output returned at block height `h` therefore appears spendable even though Bitcoin consensus prevents spending it until `h + 100`.

### Finding Description
`ReceivedOutput` contains only an offset, `TxOut`, and outpoint; it carries no maturity state or indication that the output is currently unspendable. [1](#0-0)  `scan_transaction` matches only `output.script_pubkey` and constructs a spendable-looking `ReceivedOutput` for each match. [2](#0-1)  `scan_block` invokes that logic for `block.txdata[0]` just like every other transaction and does not enforce the coinbase maturity rule. [3](#0-2) 

### Impact Explanation
An unprivileged miner can create a valid block whose coinbase pays the monitored key. The scanner reports those funds as received immediately, but any transaction attempting to spend the returned `ReceivedOutput` will be consensus-invalid for 100 blocks. This creates false balance/accounting and may trigger attempted spends of unavailable funds.

### Likelihood Explanation
Exploitation requires a miner-controlled coinbase output addressed to the scanned key. While obtaining a coinbase requires producing a block, the vulnerable input is a public Bitcoin block and no trusted setup, proof, or privileged caller is required by `scan_block`. The function’s own comment acknowledges that post-processing is required if outputs must be immediately spendable, but that obligation is not enforced by the API. [4](#0-3) 

### Recommendation
Change `scan_block` to skip `block.txdata[0]`, or extend `ReceivedOutput` with maturity metadata and expose only mature outputs through APIs representing spendable funds. If preserving the current API for auditing purposes, rename or document it as returning non-spendable candidate outputs and provide a separate `scan_spendable_block` that omits coinbase transactions.

### Proof of Concept
```rust
// Given `block` where `block.txdata[0]` is the coinbase transaction and
// `block.txdata[0].output[0].script_pubkey` matches the scanner's key:
let outputs = scanner.scan_block(&block);

// The immature coinbase output is returned as an ordinary ReceivedOutput.
assert_eq!(outputs[0].outpoint().vout, 0);
assert_eq!(outputs[0].outpoint().txid, block.txdata[0].compute_txid());
```

A downstream caller can pass this `ReceivedOutput` to transaction construction, but Bitcoin consensus rejects the spend until the coinbase has matured.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L90-97)
```rust
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
