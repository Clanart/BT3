### Title
Immature coinbase outputs are reported as spendable `ReceivedOutput`s - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`Scanner::scan_block` scans every transaction in `block.txdata`, including the coinbase transaction at index zero, and returns matching outputs as spendable `ReceivedOutput`s without checking block depth or coinbase maturity. [1](#0-0)  An immature coinbase output can therefore be reported as available funds even though Bitcoin consensus temporarily prevents it from being spent. [2](#0-1) 

### Finding Description
`ReceivedOutput` is explicitly modeled as “a spendable output.” [3](#0-2)  `Scanner::scan_transaction` returns a `ReceivedOutput` for every transaction output whose `script_pubkey` matches a registered scanner script, with no transaction-position or maturity check. [4](#0-3)  `Scanner::scan_block` applies that logic to all of `block.txdata`, including `block.txdata[0]`; its comment acknowledges that coinbase outputs are maturity-bound but leaves exclusion to callers. [1](#0-0) 

Downstream, `SignableTransaction::new` accepts each returned `ReceivedOutput`, adds its value to available input funds, and uses its outpoint as an input. [5](#0-4)  There is no height, confirmation-count, or coinbase flag in `ReceivedOutput`, so downstream code cannot distinguish an immature coinbase output from an ordinary spendable UTXO. [3](#0-2) 

### Impact Explanation
A block paying the scanner’s Taproot script in its coinbase transaction produces a `ReceivedOutput` that appears spendable but cannot actually be spent until the coinbase matures. [1](#0-0)  This can cause wallet accounting to report unavailable funds as spendable and can cause transaction construction or threshold signing to proceed for a transaction Bitcoin nodes will reject while the coinbase remains immature. [5](#0-4) 

### Likelihood Explanation
This is reachable through public Bitcoin block data passed to `Scanner::scan_block`. [6](#0-5)  The triggering output must be a coinbase paying a registered scanner script, so exploitation generally requires mining a block or supplying scanner input from an untrusted/unvalidated block source; ordinary non-mining transaction senders cannot directly create a valid coinbase output.

### Recommendation
Do not return coinbase outputs from `Scanner::scan_block` until maturity information is available, or carry the transaction’s coinbase status and block height in `ReceivedOutput` and reject immature outputs in `SignableTransaction::new`. [1](#0-0)  At minimum, skip `block.txdata[0]` in `scan_block` and provide a separate API that records coinbase receipts without representing them as immediately spendable. [6](#0-5) 

### Proof of Concept
```rust
// `key` is the scanner's group public key and `script` is its P2TR script.
let scanner = Scanner::new(key).unwrap();
let script = p2tr_script_buf(key).unwrap();

// An untrusted block whose coinbase pays the scanner.
let mut block: Block = obtain_block();
block.txdata[0].output[0].script_pubkey = script;

// This returns the immature coinbase output as a supposedly spendable output.
let received = scanner.scan_block(&block);
assert_eq!(received.len(), 1);

// The wallet then accepts the immature output as a spendable input.
let tx = SignableTransaction::new(
    received,
    &payments,
    change,
    None,
    fee_per_vbyte,
);
```

`scan_block` reaches the coinbase because it iterates over the entire `block.txdata` slice. [6](#0-5)  `scan_transaction` then creates the matching `ReceivedOutput`, while `SignableTransaction::new` consumes it without any maturity validation. [4](#0-3) [5](#0-4)

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
