### Title
Coinbase outputs are reported as immediately spendable received funds - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`Scanner::scan_block` scans `block.txdata[0]`, including the coinbase transaction, and returns matching outputs as `ReceivedOutput` values despite Bitcoin's coinbase maturity restriction. [1](#0-0) 

### Finding Description
`ReceivedOutput` is explicitly defined as “a spendable output,” while `scan_transaction` unconditionally converts every matching `script_pubkey` into a `ReceivedOutput` containing its value and outpoint. [2](#0-1) [3](#0-2) 

`scan_block` iterates over every transaction in `block.txdata`, including index `0`, which is the coinbase transaction in a valid Bitcoin block. [4](#0-3) 

Although the comment acknowledges that the coinbase is bound by maturity, the returned `ReceivedOutput` contains no maturity marker and cannot be distinguished from an immediately spendable output. [5](#0-4) 

### Impact Explanation
A miner or other unprivileged party capable of getting a block accepted can pay the scanned Taproot script in the coinbase and cause the wallet scanner to report those funds as received before they can legally be spent. [6](#0-5) 

A caller using `scan_block` as instructed by its API can pass the resulting `ReceivedOutput` to `SignableTransaction::new`, which treats its value and outpoint as spendable input data. [7](#0-6) [8](#0-7) 

This produces the concrete failure mode of funds reported received that are not spendable, and any transaction constructed from the immature outpoint is consensus-invalid until maturity. [5](#0-4) 

### Likelihood Explanation
The condition only requires a valid block whose coinbase pays a registered scanner script, and the vulnerable path is the normal `scan_block` API rather than malformed input or an internal invariant violation. [4](#0-3) 

The issue does not let an arbitrary non-miner create a coinbase output, so exploitation depends on miner behavior or a miner paying the monitored address. [6](#0-5) 

### Recommendation
Change `scan_block` to skip `block.txdata[0]` by default, matching the documented caller-side workaround. [5](#0-4) 

If coinbase scanning is required, return an output type carrying its maturity height or otherwise prevent it from being accepted by transaction-construction APIs until it is mature. [2](#0-1) 

### Proof of Concept
```rust
use bitcoin::{Block, Transaction, TxOut, Amount, OutPoint, TxIn, ScriptBuf};
use bitcoin::transaction::Version;
use bitcoin::absolute::LockTime;
use bitcoin::Sequence;
use k256::{ProjectivePoint, Scalar};
use bitcoin_serai::wallet::{p2tr_script_buf, Scanner};

let key = ProjectivePoint::GENERATOR * Scalar::ONE;
let scanner = Scanner::new(key).unwrap();

// A coinbase-like transaction paying the scanner's registered script.
let coinbase = Transaction {
  version: Version(2),
  lock_time: LockTime::ZERO,
  input: vec![TxIn {
    previous_output: OutPoint::null(),
    script_sig: ScriptBuf::new(),
    sequence: Sequence::MAX,
    witness: Default::default(),
  }],
  output: vec![TxOut {
    value: Amount::from_sat(50_0000_0000),
    script_pubkey: p2tr_script_buf(key).unwrap(),
  }],
};

let block = Block {
  header: /* valid block header */,
  txdata: vec![coinbase],
};

let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].value(), 50_0000_0000);

// This output is returned as a normal `ReceivedOutput` even though its
// coinbase outpoint cannot be spent until it reaches maturity.
```

The scanner currently reaches this result because `scan_block` feeds `block.txdata[0]` into `scan_transaction`, and `scan_transaction` accepts the matching script without checking whether the containing transaction is a coinbase. [6](#0-5)

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

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
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
