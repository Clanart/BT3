### Title
Coinbase outputs are reported as spendable before maturity - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

`Scanner::scan_block` scans every transaction in a block, including the coinbase transaction, and returns matching outputs as `ReceivedOutput` values documented as spendable. [1](#0-0) [2](#0-1) 

### Finding Description

`ReceivedOutput` represents an output that the wallet can later convert into a transaction input. [1](#0-0) [3](#0-2) 

`Scanner::scan_block` explicitly notes that coinbase outputs are bound by maturity, but still iterates over `block.txdata` from index zero and calls `scan_transaction` on the coinbase. [2](#0-1) 

A miner can therefore place a Taproot output paying to the scanner's registered script in the coinbase transaction, and `scan_block` will return it as an ordinary received output even though consensus rules do not currently allow it to be spent. [4](#0-3) [2](#0-1) 

### Impact Explanation

The scanner reports funds as received that are not spendable at the reported height. [1](#0-0) [2](#0-1) 

A downstream caller can pass the returned `ReceivedOutput` into `SignableTransaction::new`, which converts it into a transaction input without checking whether its source transaction was an immature coinbase. [5](#0-4) [3](#0-2) 

This is a Medium-severity availability and accounting flaw because it requires a miner to create the relevant coinbase output and primarily causes the victim to build a consensus-invalid spend or misaccount unavailable funds. [2](#0-1) 

### Likelihood Explanation

The trigger is reachable through an untrusted Bitcoin block supplied to `scan_block`, and no signature, validator privilege, leaked key, or malformed encoding is required. [6](#0-5) 

Exploitation requires control of a mined block and knowledge of a registered scanner script, so it is less broadly reachable than an arbitrary network sender but does not require insider access. [4](#0-3) 

### Recommendation

Do not return outputs from `block.txdata[0]` in `Scanner::scan_block`, or attach coinbase/maturity metadata to `ReceivedOutput` and reject immature outputs before `SignableTransaction::new` accepts them. [2](#0-1) [5](#0-4) 

### Proof of Concept

```rust
// Construct a block whose txdata[0] is a coinbase transaction containing:
// tx.output[0].script_pubkey == scanner_script registered in Scanner.scripts.

let received = scanner.scan_block(&block);

// Current behavior: returns a ReceivedOutput for the immature coinbase output.
assert_eq!(received.len(), 1);
assert_eq!(received[0].outpoint().vout, 0);

// The caller can then attempt to spend the unavailable output.
let signable = SignableTransaction::new(
  received,
  &[(payment_script, DUST)],
  None,
  None,
  fee_per_vbyte,
);
```

`scan_block` reaches this result because it scans every transaction in `block.txdata` rather than skipping the coinbase. [2](#0-1)

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
