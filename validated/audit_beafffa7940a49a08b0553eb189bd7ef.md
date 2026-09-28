### Title
Coinbase outputs are reported as spendable before maturity - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`Scanner::scan_block` treats every transaction in a block identically, including `block.txdata[0]`—the coinbase transaction. `Scanner::scan_transaction` creates a `ReceivedOutput` solely because an output's `script_pubkey` matches a registered scanner script. Because `ReceivedOutput` is defined as “a spendable output,” an immature coinbase output is incorrectly surfaced as spendable even though Bitcoin consensus forbids spending it for 100 blocks. [1](#0-0) [2](#0-1) 

### Finding Description
`scan_transaction` matches `output.script_pubkey` against `self.scripts` and constructs a `ReceivedOutput` containing the registered offset, transaction output, and outpoint. [3](#0-2) 

`scan_block` calls `scan_transaction` for every `block.txdata` entry, with no exclusion of the coinbase transaction or maturity metadata. [4](#0-3) 

The comment acknowledges that coinbase outputs are maturity-bound, but still returns them through an API whose result type promises spendable outputs. [1](#0-0) [5](#0-4) 

### Impact Explanation
An unprivileged miner can place an output paying to the scanner’s Taproot script in the coinbase transaction. When the block is scanned, Serai reports that output as a `ReceivedOutput`, even though any transaction spending it before maturity is consensus-invalid. This can cause downstream wallet/signing logic to select an unusable input, produce a transaction rejected by the network, and temporarily make the reported balance unavailable for correct spending. [2](#0-1) 

### Likelihood Explanation
Any miner can trigger this using only a valid public Bitcoin block; no validator keys, malformed encodings, or privileged protocol state are required. The condition also occurs naturally whenever mining pays the tracked address, making it reachable under ordinary operation. [4](#0-3) 

### Recommendation
`scan_block` should skip `block.txdata[0]` by default or propagate coinbase/maturity metadata so consumers cannot treat the result as immediately spendable. If immature outputs must be returned, `ReceivedOutput` should carry an explicit spendability state or earliest-spendable height rather than presenting itself as unconditionally spendable. [4](#0-3) 

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs

let mut scanner = Scanner::new(even_key).unwrap();

// `coinbase_tx` is `block.txdata[0]` and pays to the scanner's
// registered P2TR script.
let block = Block {
    header,
    txdata: vec![coinbase_tx, other_tx],
};

let received = scanner.scan_block(&block);

// This succeeds because scan_block does not exclude txdata[0].
assert!(received.iter().any(|output|
    output.outpoint() == &OutPoint::new(coinbase_tx.compute_txid(), vout)
));

// The returned ReceivedOutput cannot be spent until coinbase maturity,
// despite ReceivedOutput being the API's spendable-output type.
```

The vulnerable path is direct: `scan_block` iterates all transactions, `scan_transaction` accepts the matching `script_pubkey`, and the result is exposed as `ReceivedOutput` without checking coinbase maturity. [2](#0-1)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-227)
```rust
  /// Scan a transaction.
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
