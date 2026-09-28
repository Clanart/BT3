### Title
OP_RETURN data is excluded from fee and weight accounting - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` adds an `OP_RETURN` output to the transaction but calculates `needed_fee`, transaction weight, and change using only payment outputs and the optional change output. The extra serialized `OP_RETURN` output is therefore never included in the fee calculation.

### Finding Description
`calculate_weight_vbytes` constructs its fee-estimation transaction from `payments` plus, optionally, one change output. It has no parameter for the `data`/`OP_RETURN` output and never appends one. [1](#0-0) 

`SignableTransaction::new` nevertheless appends an `OP_RETURN` output containing up to 80 bytes of caller-controlled data. [2](#0-1) 

The initial fee and weight are then calculated without that output. [3](#0-2) 

When change is enabled, the change value is calculated as `input_sat - payment_sat - fee_with_change`, where `fee_with_change` also excludes the `OP_RETURN` output. [4](#0-3) 

### Impact Explanation
The actual transaction is larger than the transaction used for fee estimation. As a result:

- `needed_fee()` reports a fee that is too low for the requested `fee_per_vbyte`.
- The change output is overvalued by the fee required for the `OP_RETURN` output.
- A transaction constructed near the minimum relay fee can fall below the minimum relay rate once the `OP_RETURN` output is added.
- The resulting transaction can remain unconfirmed, leaving the spent inputs unavailable for other transactions until the invalid/stuck spend is abandoned or replaced.

This is reachable through public transaction data supplied as `data` to `SignableTransaction::new`.

### Likelihood Explanation
Any caller that supplies non-empty `data` triggers the mismatch. The maximum payload is 80 bytes, so the omitted weight is bounded, but it is sufficient to materially change the effective fee rate—especially for transactions constructed at approximately `1 sat/vbyte`.

The issue does not require malicious validators, leaked keys, or invalid curve inputs; it only requires ordinary public transaction bytes.

### Recommendation
Include the `OP_RETURN` output in `calculate_weight_vbytes`. Pass the data payload or a constructed `TxOut` into both fee-estimation calls so `needed_fee`, minimum-relay validation, and change calculation account for the complete transaction.

The fix should preserve the existing property that the dummy transaction used for estimation has the same output-count and serialized-size characteristics as the transaction ultimately signed.

### Proof of Concept
Conceptually:

1. Create a spendable `ReceivedOutput`.
2. Call:

```rust
SignableTransaction::new(
  vec![input],
  &[(payment_script, payment_amount)],
  Some(change_script),
  Some(vec![0; 80]),
  fee_per_vbyte,
)
```

3. Observe that `tx_outs` contains the `OP_RETURN` output while both calls to `calculate_weight_vbytes` account only for `payments` and `change`.
4. Compare `needed_fee()` with `fee_per_vbyte * actual_vsize`; the reported needed fee is lower than required for the serialized transaction.
5. When `fee_per_vbyte` is near Bitcoin’s minimum relay rate, the effective fee rate of the final transaction can fall below the minimum relay threshold.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L61-99)
```rust
impl SignableTransaction {
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

**File:** networks/bitcoin/src/wallet/send.rs (L193-202)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-212)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
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
