### Title
OP_RETURN data is excluded from fee and weight accounting - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an OP_RETURN output for caller-supplied `data`, but calculates transaction weight, virtual size, and required fee using only `payments` and optional change. [1](#0-0)  As a result, a public caller can include up to 80 bytes of transaction data without paying for its additional virtual size. [2](#0-1) 

### Finding Description
`calculate_weight_vbytes` constructs a transaction containing inputs, payment outputs, and optionally a change output, but has no parameter representing the OP_RETURN output. [3](#0-2)  Although the constructor adds the data output to `tx_outs`, both the initial fee calculation and the change-enabled fee calculation pass only `payments` to `calculate_weight_vbytes`. [1](#0-0) [4](#0-3)  The stored `needed_fee` is therefore `fee_per_vbyte` multiplied by the size of a smaller transaction than the one that will actually be signed. [5](#0-4)  The maximum-standard-weight check also uses the understated `weight`, so attacker-supplied data can additionally move a near-limit transaction over the relay-size boundary after validation. [6](#0-5) 

### Impact Explanation
An unprivileged caller controls the public `data` argument and can force the wallet to build and sign a transaction whose effective fee rate is lower than the requested `fee_per_vbyte`. [7](#0-6)  If the input value exactly covers `payment_sat + needed_fee`, the actual transaction fee equals the underestimated `needed_fee`, while the final serialized transaction is larger than the transaction used to calculate that fee. [8](#0-7)  At low requested rates this can produce a transaction below the minimum relay rate even though `TooLowFee` was checked, and near `MAX_STANDARD_TX_WEIGHT` it can produce a transaction exceeding the checked standardness bound. [9](#0-8) [6](#0-5)  The resulting signed transaction may fail propagation or confirmation despite the caller paying for a larger logical payload than was priced.

### Likelihood Explanation
The issue is directly reachable through the public `SignableTransaction::new` API by supplying `Some(data)`, with data sizes up to 80 bytes accepted. [7](#0-6) [2](#0-1)  No malformed encodings, compromised peers, or invalid keys are required; ordinary transaction-construction inputs trigger the accounting discrepancy whenever `data` is present. [1](#0-0)  The discrepancy is larger when a change output is retained because excess input value is returned to change using the same understated `fee_with_change`, rather than accidentally increasing the paid fee. [4](#0-3) 

### Recommendation
Pass the serialized OP_RETURN output—or a `data` length parameter—to `calculate_weight_vbytes` and include it in both the no-change and with-change size calculations. [3](#0-2)  Perform the minimum-fee and maximum-weight checks against the final transaction containing every output that will be signed, including `data`. [9](#0-8) [6](#0-5)  Add tests asserting that `needed_fee == fee_per_vbyte * transaction_vsize` and that `weight <= MAX_STANDARD_TX_WEIGHT` for transactions carrying a maximum-length OP_RETURN output.

### Proof of Concept
```rust
// From networks/bitcoin/src/wallet/send.rs
let data = vec![0u8; 80];
let fee_per_vbyte = 1;

let tx = SignableTransaction::new(
  vec![input],                       // input.value == payment_amount + estimated_fee
  &[(payment_script, payment_amount)],
  Some(change_script),
  Some(data),
  fee_per_vbyte,
)
.unwrap();

// The final transaction contains the OP_RETURN output.
assert!(tx.transaction().output.iter().any(|output| output.script_pubkey.is_op_return()));

// But its reported fee was calculated before that output was included.
assert!(
  tx.needed_fee() <
    u64::try_from(tx.transaction().vsize()).unwrap() * fee_per_vbyte
);
```

This succeeds because `tx_outs` receives the OP_RETURN output before the size calculation, while `calculate_weight_vbytes` is invoked with only `payments` and `change`. [1](#0-0) [4](#0-3)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L62-66)
```rust
  fn calculate_weight_vbytes(
    inputs: usize,
    payments: &[(ScriptBuf, u64)],
    change: Option<&ScriptBuf>,
  ) -> (u64, u64) {
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

**File:** networks/bitcoin/src/wallet/send.rs (L171-173)
```rust
    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L193-212)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-220)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-232)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-242)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
```
