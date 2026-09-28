### Title
`OP_RETURN` outputs are omitted from fee weight calculation, causing signed transactions to underpay the minimum relay fee - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` appends an attacker-controlled `OP_RETURN` output to the transaction, but calculates both the base and change-aware virtual sizes using only `payments`, excluding that output. As a result, a transaction can be constructed and signed with an actual vsize greater than the vsize used to derive `needed_fee`, leaving its real fee rate below the requested rate and potentially below Bitcoin's standard relay minimum.

### Finding Description
When `data` is supplied, `SignableTransaction::new` pushes a zero-value `OP_RETURN` output into `tx_outs` before estimating transaction weight. [1](#0-0)  The subsequent fee calculation calls `calculate_weight_vbytes(tx_ins.len(), payments, None)`, passing only the payment outputs and no representation of the already-added `OP_RETURN` output. [2](#0-1)  The change-output path repeats this mismatch by calling the same estimator with `payments` and `Some(&change)`, again excluding the `OP_RETURN` output that remains in the final transaction. [3](#0-2)  The final `SignableTransaction` nevertheless contains `tx_outs`, including the omitted `OP_RETURN` output. [4](#0-3) 

### Impact Explanation
An unprivileged caller who can cause transaction data to be signed can supply up to 80 bytes of `data`, adding an `OP_RETURN` output whose serialized size is not charged for. [5](#0-4)  If `fee_per_vbyte` is selected so the incorrectly small estimate just passes the minimum-relay check, the produced transaction's larger actual vsize can make its effective fee rate fall below the minimum relay rate. [6](#0-5)  The threshold machines then sign the resulting transaction as constructed, producing a valid signature over a transaction that relays poorly or not at all. [7](#0-6) 

### Likelihood Explanation
The defect is deterministic whenever `data` is supplied because the `OP_RETURN` output is always appended before both fee-size estimates and is absent from every `calculate_weight_vbytes` call. [8](#0-7)  The gap is bounded by the enforced 80-byte data limit plus output overhead, but that is enough to materially reduce the actual fee rate for a small transaction. [9](#0-8) 

### Recommendation
Calculate weight and virtual size from the complete `tx_outs`, or extend `calculate_weight_vbytes` to accept the optional `data` output alongside `payments` and `change`. Recompute the base fee, minimum-relay check, and change-aware fee using the same output set that will be committed to `SignableTransaction.tx`. [10](#0-9)  Add a regression test asserting that `needed_fee == fee_per_vbyte * actual_vsize` for transactions containing maximal `OP_RETURN` data, both with and without change.

### Proof of Concept
Conceptually, call `SignableTransaction::new` with one sufficiently funded `ReceivedOutput`, one dust-compliant payment, a change script, an 80-byte `data` value, and `fee_per_vbyte` equal to the minimum intended relay rate. [11](#0-10)  `new` appends the `OP_RETURN` output, computes `vbytes` from a transaction containing only the payment, and accepts the result if that underestimated fee passes the minimum check. [12](#0-11)  With change, it calculates change from another estimate that still excludes the `OP_RETURN` output. [3](#0-2)  Comparing `needed_fee()` with the signed transaction's actual `vsize()` shows `needed_fee() / actual_vsize < fee_per_vbyte`, despite the API documenting `needed_fee` as the fee required for the requested rate. [13](#0-12)

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

**File:** networks/bitcoin/src/wallet/send.rs (L187-213)
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

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
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

**File:** networks/bitcoin/src/wallet/send.rs (L270-284)
```rust
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
