### Title
OP_RETURN data is omitted from fee estimation, producing underpriced Bitcoin transactions - ([File: `networks/bitcoin/src/wallet/send.rs`](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` adds a caller-supplied OP_RETURN output but calculates transaction weight using only the payment outputs and optional change. The resulting `needed_fee` and change amount are based on a transaction smaller than the one actually signed and broadcast.

### Finding Description
The constructor accepts up to 80 bytes of caller-controlled `data` and appends it as an OP_RETURN transaction output before fee calculation. [1](#0-0)  However, `calculate_weight_vbytes` is invoked with only `payments`, and its internal transaction contains only those payments plus optional change; it never receives or serializes the OP_RETURN output. [2](#0-1) [3](#0-2) 

The same stale model is reused when deciding whether a change output is economical, so `fee_with_change` also excludes the data output's size. [4](#0-3)  The final transaction nevertheless retains the OP_RETURN output, while `fee()` reports only the absolute input-minus-output amount and does not account for the larger actual virtual size. [5](#0-4) [6](#0-5) 

### Impact Explanation
For transactions near the requested or minimum relay fee rate, the signed transaction has a larger vsize than the fee calculation assumed and therefore pays a lower effective fee rate. A transaction that passed `SignableTransaction::new`'s minimum-fee check can consequently be rejected by Bitcoin relay policy or remain unconfirmed. [3](#0-2) 

When change is enabled, the change output is also calculated from the underestimated fee model, encoding an amount inconsistent with the intended fee rate for the final transaction. [4](#0-3) 

### Likelihood Explanation
Any public caller of `SignableTransaction::new` can supply `data` up to 80 bytes and trigger the mismatch; the constructor explicitly permits this input. [7](#0-6)  The issue is deterministic whenever `data` is present, but it is most likely to prevent relay when `fee_per_vbyte` is at or near the minimum relay rate. [8](#0-7) 

### Recommendation
Calculate weight from the complete output list, including the OP_RETURN output, rather than only from `payments`. Alternatively, pass the optional data output into `calculate_weight_vbytes`, include it in both the no-change and with-change estimates, and validate the final transaction's actual vsize against `fee_per_vbyte` and the minimum relay fee before returning the `SignableTransaction`.

### Proof of Concept
Conceptually, construct a transaction with one spendable input, one dust-sized payment, a change script, `fee_per_vbyte = 1`, and `data = vec![0; 80]`.

```rust
let tx = SignableTransaction::new(
  vec![input],
  &[(payment_script, DUST)],
  Some(change_script),
  Some(vec![0; 80]),
  1,
)?;
```

The OP_RETURN output is added to `tx_outs`, but both weight calculations omit it. [9](#0-8)  `needed_fee` is therefore approximately the vsize without the OP_RETURN output, while the signed transaction includes roughly nine output bytes plus the pushed data. [10](#0-9) [11](#0-10)  Because `fee()` remains the absolute input-output difference, the broadcast transaction's fee rate falls below one satoshi per actual vbyte despite passing the constructor's minimum-fee check. [5](#0-4) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-99)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L137-140)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-233)
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-254)
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
```
