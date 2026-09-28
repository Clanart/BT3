### Title
`SignableTransaction::new` excludes the OP_RETURN output from fee and change calculations - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts up to 80 bytes of caller-controlled data and appends it as an OP_RETURN output, but both transaction-weight estimates are calculated only from `payments` and optional change. The resulting `needed_fee` and change amount are therefore based on a stale transaction shape that omits the data output. A signed transaction can consequently have a lower actual fee rate than requested or required for relay.

### Finding Description
`SignableTransaction::new` appends an OP_RETURN `TxOut` when `data` is present. [1](#0-0)  However, the initial weight and vbyte estimate calls `calculate_weight_vbytes` with `payments`, not the completed `tx_outs` containing that OP_RETURN output. [2](#0-1) 

The change path repeats the same incomplete estimate: `fee_with_change` is calculated from inputs, payments, and change, again without the data output already pushed into `tx_outs`. [3](#0-2) 

`needed_fee()` is documented as the fee necessary to achieve the requested fee rate. [4](#0-3)  When change is created, the actual fee is fixed to that underestimated value by reducing the change output, while the final transaction remains larger because it contains the OP_RETURN output. [5](#0-4) 

### Impact Explanation
A caller relying on `needed_fee` or supplying a change output can produce a validly signed transaction whose actual fee rate is below the requested `fee_per_vbyte` and potentially below Bitcoin's minimum relay rate. The transaction may fail propagation and the selected inputs remain unusable until a replacement transaction is constructed and signed. [6](#0-5) 

### Likelihood Explanation
The defect is reachable through public transaction-construction input: an unprivileged caller can provide `data` to `SignableTransaction::new`, which permits up to 80 bytes before returning `TooMuchData`. [7](#0-6)  It triggers whenever that data is present and the transaction uses the calculated fee to determine change, with no malformed cryptographic input or privileged operation required. [8](#0-7) 

### Recommendation
Calculate weight and virtual size from the final output list, including the OP_RETURN output, both with and without change. At minimum, change `calculate_weight_vbytes` to accept the complete `tx_outs`/output descriptors rather than only `payments`, and invoke it only after all fixed outputs have been added. [9](#0-8) 

### Proof of Concept
```rust
use bitcoin::ScriptBuf;
use bitcoin_serai::SignableTransaction;

// Constructed from a UTXO returned by Scanner, abbreviated here.
let inputs = vec![received_output];
let payments = vec![(payment_script, payment_amount)];
let change = Some(change_script);
let data = Some(vec![0xaa; 80]);

let tx = SignableTransaction::new(
    inputs,
    &payments,
    change,
    data,
    bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE.into(),
)
.unwrap();

// `needed_fee` was priced without the OP_RETURN output.
let estimated_vbytes_containing_op_return = tx.transaction().vsize() as u64;
assert!(tx.needed_fee() < estimated_vbytes_containing_op_return);
```

The OP_RETURN output is included in the final transaction, but is absent from the size estimate used to derive `needed_fee` and `change`. [8](#0-7)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L62-99)
```rust
  fn calculate_weight_vbytes(
    inputs: usize,
    payments: &[(ScriptBuf, u64)],
    change: Option<&ScriptBuf>,
  ) -> (u64, u64) {
    // Expand this a full transaction in order to use the bitcoin library's weight function
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
        inputs
      ],
      output: payments
        .iter()
        // The payment is a fixed size so we don't have to use it here
        // The script pub key is not of a fixed size and does have to be used here
        .map(|payment| TxOut {
          value: Amount::from_sat(payment.1),
          script_pubkey: payment.0.clone(),
        })
        .collect(),
    };
    if let Some(change) = change {
      // Use a 0 value since we're currently unsure what the change amount will be, and since
      // the value is fixed size (so any value could be used here)
      tx.output.push(TxOut { value: Amount::ZERO, script_pubkey: change.clone() });
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L129-135)
```rust
  /// Returns the fee necessary for this transaction to achieve the fee rate specified at
  /// construction.
  ///
  /// The actual fee this transaction will use is `sum(inputs) - sum(outputs)`.
  pub fn needed_fee(&self) -> u64 {
    self.needed_fee
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L149-173)
```rust
  /// If data is specified, an OP_RETURN output will be added with it.
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L193-233)
```rust
    // Add the OP_RETURN output
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

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
```
