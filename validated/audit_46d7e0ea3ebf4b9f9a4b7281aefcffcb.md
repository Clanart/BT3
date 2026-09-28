### Title
OP_RETURN data is excluded from transaction fee and weight calculations - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` adds the caller-controlled `data` argument as an `OP_RETURN` transaction output, but both the initial fee calculation and the change-aware recalculation measure a synthetic transaction built only from `payments` and `change`. The resulting `needed_fee`, change amount, and maximum-weight check therefore correspond to a smaller transaction than the one subsequently signed. A transaction carrying an 80-byte `OP_RETURN` output can be signed with less than the requested fee rate and can fall below Bitcoin’s minimum relay fee.

### Finding Description
The constructor accepts up to 80 bytes of caller-supplied data and appends it to `tx_outs` as a zero-valued `OP_RETURN` output. [1](#0-0) [2](#0-1) 

However, `calculate_weight_vbytes` is then called with `payments`, not the complete `tx_outs` list containing that output. [3](#0-2)  The same omission occurs when the presence of a change output is evaluated. [4](#0-3) 

Because `calculate_weight_vbytes` reconstructs the measured transaction directly from `payments` and `change`, the `OP_RETURN` output contributes no bytes to either calculated vsize. [5](#0-4)  The finalized `Transaction` nevertheless contains the additional output. [6](#0-5) 

### Impact Explanation
When a change output is present, the actual fee is fixed to the incorrectly small `needed_fee`: the change amount is `inputs - payments - fee_without_data`, while the signed transaction is larger by the serialized `OP_RETURN` output. The effective fee rate is consequently lower than `fee_per_vbyte`.

At the boundary relay fee, this produces a signed transaction below Bitcoin’s minimum relay feerate. More generally, any user-supplied transaction data causes the wallet to underpay relative to the requested rate and to return an inaccurate `needed_fee`. The maximum-standard-weight validation also omits the data output, although the 80-byte limit bounds that particular discrepancy.

### Likelihood Explanation
Any caller able to cause nonzero transaction data to be signed can reach this path through the public `data` parameter. The issue is deterministic for every non-`None` data value, including empty data, because even an empty push still introduces an additional serialized output. The largest underpayment is limited by the 80-byte payload bound, but only tens of vbytes are needed to cross a minimum-relay boundary.

### Recommendation
Pass the complete output list, including the `OP_RETURN` output, into the weight/vsize calculation. A straightforward fix is to make `calculate_weight_vbytes` accept `&[TxOut]` instead of `payments`, call it with `tx_outs`/`tx_outs + change`, and derive `needed_fee` and the maximum-weight check from that exact transaction shape.

### Proof of Concept
Conceptual execution:

```rust
let data = vec![0u8; 80];

let tx = SignableTransaction::new(
    vec![input],
    &[],
    Some(change_script),
    Some(data),
    1, // sat/vbyte
).unwrap();

// This was calculated without the OP_RETURN output.
let intended_fee = tx.needed_fee();

// The finalized transaction includes the OP_RETURN output.
let actual_vbytes = tx.transaction().vsize() as u64;
let actual_fee = tx.fee();

assert_eq!(actual_fee, intended_fee);
assert!(actual_fee < actual_vbytes);
```

The final transaction therefore has an effective fee rate below 1 sat/vbyte even though `SignableTransaction::new` accepted `fee_per_vbyte = 1` and reports `needed_fee()` as if it satisfied that rate.

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

**File:** networks/bitcoin/src/wallet/send.rs (L171-202)
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

**File:** networks/bitcoin/src/wallet/send.rs (L245-255)
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
```
