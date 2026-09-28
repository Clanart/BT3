### Title
`SignableTransaction::new` omits `OP_RETURN` outputs from fee calculation - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary

`SignableTransaction::new` accepts up to 80 bytes of caller-controlled data and adds it as an `OP_RETURN` transaction output, but calculates `needed_fee` using only the payment outputs and optional change output. The resulting transaction is larger than estimated while paying only the originally calculated fee, so its effective fee rate is lower than requested and can fall below Bitcoin’s default relay minimum. [1](#0-0) 

### Finding Description

The constructor validates the `data` length and appends a zero-valued `OP_RETURN` output to `tx_outs`. [2](#0-1) 

Afterward, it calls `calculate_weight_vbytes` with only the input count, `payments`, and no change output; the `data` output is not represented in this fee-estimation transaction. [3](#0-2) 

`calculate_weight_vbytes` derives weight and virtual size solely from the supplied payments and optional change, so every byte of the additional `OP_RETURN` output is excluded from the estimate. [4](#0-3) 

The same omission occurs when change is added: the second estimate includes the change output but still excludes the already-created `OP_RETURN` output. [5](#0-4) 

### Impact Explanation

An unprivileged caller who can cause transaction data to be included can make the wallet sign a Bitcoin transaction with less fee per virtual byte than requested. At the minimum accepted fee rate, any non-empty `data` output makes the final transaction fall below the default relay fee floor, causing standard Bitcoin nodes to reject or fail to propagate it. [6](#0-5) 

This mirrors the missing-relayer-fee class: the transfer is locally constructed and may be signed as valid, but the network lacks sufficient fee incentive/requirement to process it. The affected payment can remain unconfirmed rather than reaching its recipient. [7](#0-6) 

### Likelihood Explanation

The trigger requires only a non-empty `data` argument, which the public constructor explicitly supports and permits up to 80 bytes. [8](#0-7) 

The underpayment is deterministic whenever data is supplied, because the data output is added before fee estimation but not passed to the estimator. A configured rate of 1 sat/vbyte is sufficient to create a transaction below the default 1000 sat/kvbyte relay minimum; larger payloads and larger requested fees merely reduce the requested-versus-actual fee-rate discrepancy without necessarily preventing relay. [9](#0-8) 

### Recommendation

Include the serialized `OP_RETURN` output in the transaction used by `calculate_weight_vbytes`, both with and without change. A robust fix would assemble the complete output set before fee calculation, or extend `calculate_weight_vbytes` to accept the optional data output and recalculate the fee after deciding whether the change output exists. [10](#0-9) 

The minimum-relay check should also use the final transaction’s actual virtual size rather than the payment-only estimate. [3](#0-2) 

### Proof of Concept

Conceptual test using the public API and a funded `ReceivedOutput` obtained through `Scanner`:

```rust
// networks/bitcoin/src/wallet/send.rs
let fee_rate = 1;
let data = vec![0x42; 80];

let tx = SignableTransaction::new(
  vec![funded_output],
  &[(payment_script, 10_000)],
  None,
  Some(data),
  fee_rate,
).unwrap();

// This passes because `vbytes` excludes the OP_RETURN output.
assert_eq!(tx.needed_fee(), payment_only_vsize);

// The actual signed transaction includes the OP_RETURN output.
let actual_vsize = tx.transaction().vsize() as u64;
assert!(actual_vsize > tx.needed_fee());
assert!(tx.fee() < actual_vsize); // Below 1 sat/vbyte / 1000 sat/kvbyte.
```

At `fee_rate == 1`, the constructor’s minimum-fee check uses the underestimated payment-only virtual size. Because the zero-valued `OP_RETURN` output increases `actual_vsize` without increasing `tx.fee()`, the final transaction pays less than one satoshi per virtual byte and does not satisfy the default relay policy encoded by the function’s own check. [9](#0-8)

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

**File:** networks/bitcoin/src/wallet/send.rs (L129-141)
```rust
  /// Returns the fee necessary for this transaction to achieve the fee rate specified at
  /// construction.
  ///
  /// The actual fee this transaction will use is `sum(inputs) - sum(outputs)`.
  pub fn needed_fee(&self) -> u64 {
    self.needed_fee
  }

  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L149-155)
```rust
  /// If data is specified, an OP_RETURN output will be added with it.
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
```

**File:** networks/bitcoin/src/wallet/send.rs (L171-233)
```rust
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

    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();

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
