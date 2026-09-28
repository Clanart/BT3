### Title
Medium: OP_RETURN data is omitted from transaction fee calculation - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` appends a caller-controlled OP_RETURN output but calculates transaction weight, virtual size, and therefore `needed_fee` using only `payments`. A transaction can consequently be signed with less fee than the requested sat/vbyte rate and even below Bitcoin’s default relay threshold.

### Finding Description
`SignableTransaction::new` adds `data` to `tx_outs` as an OP_RETURN output, allowing up to 80 bytes of public transaction data. [1](#0-0) 

Both the no-change and with-change size calculations pass `payments` rather than the complete `tx_outs`, so the OP_RETURN output is absent from both fee estimates. [2](#0-1) [3](#0-2) 

The constructed transaction nevertheless uses `tx_outs`, including the OP_RETURN output. [4](#0-3) 

This is the same bug class as the reported incorrect fee formula: an amount intended to represent `rate * actual_size` is instead calculated from an incomplete denominator/input, producing a smaller fee than intended.

### Impact Explanation
An unsigned transaction constructed with `data` reports and reserves a `needed_fee` lower than the fee required for the transaction’s actual virtual size. [5](#0-4) 

When change is used, the change output also receives too much value because `fee_with_change` omits the OP_RETURN output’s size. [6](#0-5) 

At boundary fee rates, the resulting signed transaction can fall below the default relay fee rate despite passing the implementation’s `TooLowFee` check. This can prevent the FROST-signed transaction from relaying or confirming, causing a protocol-liveness failure rather than merely overpaying or underpaying an accounting fee. [7](#0-6) 

### Likelihood Explanation
The incorrect path is reachable whenever a caller supplies `Some(data)` to `SignableTransaction::new`; the code explicitly permits up to 80 bytes. [8](#0-7) [9](#0-8) 

The impact is deterministic for any nonzero data length, while the practical severity is greatest when the requested fee rate is near the minimum relay rate or the OP_RETURN output is large.

### Recommendation
Calculate weight and virtual size from the complete output vector, including the OP_RETURN output. Conceptually, `calculate_weight_vbytes` should accept the final `TxOut` list or an output list containing payments plus data, and the same list should be used for both no-change and with-change estimates. The fee and minimum-relay check should then use `fee_per_vbyte * actual_vbytes` for the transaction that will actually be signed.

### Proof of Concept
For a one-input transaction requesting `fee_per_vbyte = 1`, one payment output, one change output, and `data = vec![0; 80]`:

1. `SignableTransaction::new` first creates the payment output and appends the 80-byte OP_RETURN output to `tx_outs`. [10](#0-9) 
2. The with-change fee estimate is calculated as if only the payment and change outputs existed. [3](#0-2) 
3. The returned transaction includes all three outputs, but `needed_fee` remains approximately `1 * vbytes_without_op_return`.
4. The signed transaction’s actual virtual size is approximately 90 bytes larger than the estimate, so its real fee rate is only about `needed_fee / actual_vbytes`, substantially below 1 sat/vbyte.
5. Thus the transaction passes `SignableTransaction::new`’s minimum-fee check while the finalized signed transaction can be rejected under the default minimum relay policy.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L129-140)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L149-156)
```rust
  /// If data is specified, an OP_RETURN output will be added with it.
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
```

**File:** networks/bitcoin/src/wallet/send.rs (L171-173)
```rust
    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-202)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L224-233)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-251)
```rust
    Ok(SignableTransaction {
      tx: Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: tx_ins,
        output: tx_outs,
      },
```
