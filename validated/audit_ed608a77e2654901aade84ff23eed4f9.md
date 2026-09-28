### Title
Immature coinbase outputs are reported as spendable wallet inputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` scans every transaction in `block.txdata`, including the coinbase transaction, and returns matching coinbase outputs as ordinary `ReceivedOutput` values. [1](#0-0)  A miner can therefore cause the wallet to account for an output that cannot yet be spent, after which `SignableTransaction::new` will incorporate it into a transaction without any maturity check. [2](#0-1) 

### Finding Description
`Scanner::scan_transaction` converts every output whose `script_pubkey` matches a registered script into a `ReceivedOutput`, recording only its offset, `TxOut`, and outpoint. [3](#0-2)  `Scanner::scan_block` invokes that logic for `block.txdata` without excluding index zero or recording whether the transaction is a coinbase. [1](#0-0)  The resulting `ReceivedOutput` is then accepted by `SignableTransaction::new`, which converts it directly into a `TxIn` using `Sequence::MAX`. [2](#0-1) 

The only maturity handling is a comment telling callers to perform post-processing or manually call `scan_transaction` on `block.txdata[1 ..]`; neither `scan_block` nor `ReceivedOutput` enforces or preserves that distinction. [4](#0-3)  Consequently, code using the natural block-scanning API has no selector or output flag equivalent to “include only spendable/non-locked outputs.” [5](#0-4) 

### Impact Explanation
A miner can direct a coinbase payout to a Serai-controlled P2TR script and cause the scanner to report the payout as a normal received output. [6](#0-5)  If that output is selected for spending before coinbase maturity, the wallet constructs and threshold-signs a transaction that consensus will reject. [7](#0-6)  This can temporarily prevent construction of a valid payment plan and incorrectly report funds as immediately available. 

### Likelihood Explanation
The trigger is a valid Bitcoin block containing a coinbase output paying to a scanned script. [1](#0-0)  It requires a miner or mining pool to create the coinbase, but no private-key material, malformed encoding, malicious validator, or invalid signature is required.  Any downstream caller that uses `scan_block` directly reaches the condition because the function does not omit the coinbase transaction. [1](#0-0) 

### Recommendation
Add an explicit spendability/maturity mode or output classification to the scanning API. At minimum, `scan_block` should exclude `block.txdata[0]` by default, while any opt-in coinbase scanning should return an output type that records its immature status and cannot be passed to `SignableTransaction::new` until mature. [8](#0-7) 

### Proof of Concept
```rust
use bitcoin::{block::Block, Amount, ScriptBuf, TxOut};

fn demonstrates_coinbase_reported(
  scanner: &Scanner,
  block: &Block,
  payment: (ScriptBuf, u64),
) -> Result<(), TransactionError> {
  // scan_block includes block.txdata[0], the coinbase transaction.
  let outputs = scanner.scan_block(block);

  // A coinbase output paying to a registered script is indistinguishable
  // from a normal spendable output here.
  let immature = outputs
    .into_iter()
    .find(|output| output.outpoint().txid == block.txdata[0].compute_txid())
    .expect("coinbase payout was reported");

  // The wallet accepts it as an input and builds a spend from it.
  let signable = SignableTransaction::new(
    vec![immature],
    &[payment],
    None,
    None,
    1,
  )?;

  // The resulting transaction would be invalid until coinbase maturity.
  assert_eq!(
    signable.transaction().input[0].previous_output.txid,
    block.txdata[0].compute_txid(),
  );
  Ok(())
}
```
`scan_block` iterates over every `block.txdata` entry, including index zero. [1](#0-0)  `SignableTransaction::new` converts each supplied `ReceivedOutput` into a `TxIn` without checking whether its previous output came from a coinbase. [2](#0-1)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L198-210)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L216-225)
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
      offsets,
      prevouts: inputs.drain(..).map(|input| input.output).collect(),
      needed_fee,
    })
```
