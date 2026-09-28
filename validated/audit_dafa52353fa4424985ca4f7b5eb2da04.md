### Title
`Scanner::scan_block` reports immature coinbase payouts as spendable outputs - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`Scanner::scan_block` scans every transaction in a block, including the coinbase transaction, and returns matching outputs as `ReceivedOutput` values described by the type as spendable. [1](#0-0) [2](#0-1) 

A coinbase output is consensus-immature for 100 blocks, but neither `ReceivedOutput` nor the downstream transaction builder records or enforces maturity. [3](#0-2) [4](#0-3) 

### Finding Description
`scan_transaction` converts each output whose `script_pubkey` is registered in `scripts` into a `ReceivedOutput`, retaining only the scalar offset, transaction output, and outpoint. [5](#0-4) 

`scan_block` blindly applies this conversion to `block.txdata`, so `block.txdata[0]`—the coinbase—is treated identically to an immediately spendable transaction output. [6](#0-5) 

The only maturity-related protection is a comment requiring callers to post-process the result or manually scan `block.txdata[1 ..]`; the API still returns the immature output as a normal `ReceivedOutput`. [7](#0-6) 

When the returned value is passed to `SignableTransaction::new`, the builder checks only the number of inputs, output values, dust limits, fees, and transaction weight, while using the supplied outpoint directly as a previous output. [8](#0-7) [9](#0-8) 

`SignableTransaction::multisig` validates that the previous output’s `script_pubkey` corresponds to the threshold key and offset, but performs no check that the referenced outpoint is mature. [10](#0-9) 

### Impact Explanation
A block producer can place a valid Taproot coinbase payout to a scanned Serai key, causing `scan_block` to report funds as received and spendable even though consensus rejects any spend for 100 blocks. [11](#0-10) 

A downstream signer can construct and threshold-sign a transaction spending that reported output, but the resulting transaction cannot enter the mempool or blockchain until maturity, temporarily preventing the wallet from using that reported balance. [4](#0-3) [12](#0-11) 

This creates the same externally observable condition as the referenced issue: a redemption or withdrawal path can produce/sign a spend which is rejected because the represented funds are temporarily unavailable. [2](#0-1) 

### Likelihood Explanation
Triggering the issue requires the payout to appear in the coinbase transaction, so an attacker needs to produce or control a mined block rather than merely broadcast an ordinary transaction. [6](#0-5) 

Once such a block exists, every caller that uses the natural block-scanning API receives the immature output in the same `Vec<ReceivedOutput>` representation used for spendable inputs. [11](#0-10) 

The signing path does not independently reject the input, so the incorrect state propagates into a fully formed transaction unless the caller implements the documented filtering requirement itself. [13](#0-12) 

### Recommendation
Change `scan_block` to exclude `block.txdata[0]` by default, or return a type that carries coinbase/maturity metadata and cannot be passed directly to `SignableTransaction::new`. [6](#0-5) 

If coinbase outputs must be returned for balance tracking, add a separate `scan_block_including_coinbase` API and require maturity information or confirmation height before conversion into a spendable `ReceivedOutput`. [14](#0-13) [7](#0-6) 

### Proof of Concept
The following conceptual test demonstrates the state transition:

```rust
// `block` is a valid Bitcoin block whose coinbase pays `p2tr_script_buf(key)`.
assert!(block.txdata[0].is_coinbase());
assert_eq!(
  block.txdata[0].output[0].script_pubkey,
  p2tr_script_buf(key).unwrap()
);

let scanner = Scanner::new(key).unwrap();
let outputs = scanner.scan_block(&block);

// The immature coinbase output is returned with the same type as spendable outputs.
assert_eq!(outputs.len(), 1);
assert_eq!(
  outputs[0].outpoint(),
  &OutPoint::new(block.txdata[0].compute_txid(), 0)
);

// The transaction builder accepts it as an input because it only sees its
// value, script, and outpoint—not its coinbase maturity.
let signable = SignableTransaction::new(
  outputs,
  &[(payment_script, payment_amount)],
  change_script,
  None,
  fee_per_vbyte,
).unwrap();
```

The crucial behavior is that `scan_block` iterates `block.txdata` without excluding index zero, while `SignableTransaction::new` constructs `TxIn::previous_output` directly from the reported outpoint. [6](#0-5) [4](#0-3)

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

**File:** networks/bitcoin/src/wallet/send.rs (L150-185)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

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

**File:** networks/bitcoin/src/wallet/send.rs (L215-243)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }

    // If there's a change address, check if there's change to give it
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
    }

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L245-284)
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
  }

  /// Returns the TX ID of the transaction this will create.
  pub fn txid(&self) -> [u8; 32] {
    let mut res = self.tx.compute_txid().to_byte_array();
    res.reverse();
    res
  }

  /// Returns the transaction, sans witness, this will create if signed.
  pub fn transaction(&self) -> &Transaction {
    &self.tx
  }

  /// Create a multisig machine for this transaction.
  ///
  /// Returns None if the wrong keys are used.
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
```
