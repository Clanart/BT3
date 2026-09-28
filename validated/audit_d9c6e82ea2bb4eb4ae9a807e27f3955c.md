### Title
Scanner reports immature coinbase outputs as spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` treats every transaction in a block, including the coinbase transaction, the same way and returns matching outputs as `ReceivedOutput`s without enforcing Bitcoin’s 100-block coinbase maturity rule. The type is documented as “a spendable output,” so callers can incorrectly treat funds that consensus will not yet allow to be spent as available balance. [1](#0-0) [2](#0-1) 

### Finding Description
`Scanner::scan_block` iterates over `block.txdata` from index zero and calls `scan_transaction` for the coinbase transaction as well as normal transactions. [3](#0-2)  `scan_transaction` matches only `output.script_pubkey` against registered scripts and immediately constructs a `ReceivedOutput`; it has no transaction-position, block-height, or maturity check. [4](#0-3) 

Although the function comment mentions that a post-processing pass is needed when immediate spendability is required, the API returns the coinbase match through the same `ReceivedOutput` type explicitly described as spendable. Nothing in `ReceivedOutput` preserves that the source was an immature coinbase, so downstream wallet logic cannot distinguish it from an ordinary spendable output. [1](#0-0) [5](#0-4) 

### Impact Explanation
A block containing a coinbase output paying the scanner’s Taproot script will be reported as received before that output is spendable. Any wallet or accounting path which consumes `scan_block`’s results directly can select the immature output for `SignableTransaction::new`, produce a transaction rejected by consensus, or temporarily report unavailable funds as spendable. The practical impact is bounded to newly mined coinbase outputs and requires waiting for maturity, making this a Medium-severity correctness issue rather than direct theft.

### Likelihood Explanation
The condition occurs whenever the scanner observes a block whose coinbase pays a registered script. It is less likely than a payment through an ordinary transaction because the payer must control or influence a mined coinbase, but it is deterministically reachable through normal public Bitcoin block data. Once encountered, `scan_block` reliably emits the immature output because no maturity check exists.

### Recommendation
Change `scan_block` to skip `block.txdata[0]`, or introduce an explicit block-height/confirmation-aware API which filters coinbase outputs until they have at least 100 confirmations. If retaining the current behavior, rename or type-separate the result so callers cannot treat it as a spendable `ReceivedOutput` without applying maturity filtering.

### Proof of Concept
```rust
// Conceptual reproduction using a regtest block younger than 100 confirmations.
let key = scanner_taproot_key();
let scanner = Scanner::new(key).unwrap();

// `block` contains a coinbase transaction whose output script matches
// Scanner's registered P2TR script.
let received = scanner.scan_block(&block);

// This output is reported as spendable even though the coinbase is immature.
assert_eq!(received.len(), 1);
assert_eq!(received[0].outpoint().vout, coinbase_vout);
assert_eq!(received[0].output().script_pubkey, expected_script);

// Using `received[0]` as a `SignableTransaction` input at this height
// produces a transaction rejected by Bitcoin consensus until 100 blocks.
```

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-213)
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
