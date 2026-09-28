### Title
`SignableTransaction::new` excludes `OP_RETURN` outputs from fee and weight accounting - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` accepts caller-controlled `data` and appends an `OP_RETURN` output before performing fee and standard-weight checks. However, `calculate_weight_vbytes` is called with only `payments`, not the already-created `tx_outs`, so the `OP_RETURN` output is omitted from both the estimated virtual size and maximum-weight validation.

### Finding Description
The constructor adds an `OP_RETURN` output when `data` is supplied, after limiting the payload to 80 bytes. [1](#0-0)  It then calculates `(weight, vbytes)` using `tx_ins.len()` and `payments`, but `payments` does not include the `OP_RETURN` output. [2](#0-1)  The same omission occurs when a change output is considered: `calculate_weight_vbytes` again receives `payments`, not `tx_outs`. [3](#0-2)  Finally, the stale `weight` value is used for the `MAX_STANDARD_TX_WEIGHT` check. [4](#0-3) 

### Impact Explanation
An unprivileged caller supplying transaction data can cause a transaction to be constructed with an underestimated required fee and an unchecked additional output. If inputs exactly cover `payments + needed_fee`, the resulting transaction's actual fee is the originally estimated fee, but its real vsize is larger due to the omitted `OP_RETURN`. The transaction may therefore fail minimum relay-fee policy despite `SignableTransaction::new` succeeding. In the extreme case, a transaction near the standard weight limit can exceed `MAX_STANDARD_TX_WEIGHT` because the `OP_RETURN` weight is never included.

### Likelihood Explanation
The `data` parameter is a direct public input to `SignableTransaction::new`, and any value up to 80 bytes is accepted. [5](#0-4)  The omitted output has deterministic nonzero size, so the underestimation occurs whenever `data` is `Some`, including an empty payload. Triggering an actual relay rejection requires a fee/weight boundary condition, but no privileged behavior or malformed cryptography is needed.

### Recommendation
Pass the complete output set, including `OP_RETURN`, into weight/vsize calculation, or add the `OP_RETURN` output inside `calculate_weight_vbytes`. Recalculate the fee and weight after every output mutation, including the conditional change-output decision, and check `MAX_STANDARD_TX_WEIGHT` against the final transaction.

### Proof of Concept
Conceptually:

1. Construct valid `inputs` whose total value is exactly `payment_sat + fee_per_vbyte * vbytes_without_op_return`.
2. Provide one payment above `DUST`.
3. Provide `data = Some(vec![0; 80])`.
4. Call `SignableTransaction::new`.

The function:
- accepts the payload because it is not over 80 bytes,
- appends the `OP_RETURN` to `tx_outs`,
- computes `needed_fee` from `payments` only,
- accepts because `input_sat == payment_sat + underestimated_needed_fee`,
- returns a transaction whose actual size includes the extra `OP_RETURN` output.

The returned transaction's `fee()` will equal the underestimated `needed_fee`, while its true virtual size is larger than the size used to calculate that fee.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L150-202)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-206)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-233)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
