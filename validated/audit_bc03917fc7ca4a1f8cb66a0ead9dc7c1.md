### Title
Attacker-controlled `OP_RETURN` data is omitted from Bitcoin fee and weight accounting - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` accepts up to 80 bytes of caller-provided `data` and appends it as an `OP_RETURN` output, but calculates transaction weight, virtual size, fees, and maximum-standard-size eligibility using only `payments` and `change`. [1](#0-0) [2](#0-1) 

This can produce a signed transaction whose actual fee rate is below the requested fee rate and potentially below Bitcoin’s default relay rate when a change output is present.

### Finding Description
The public `data` parameter is validated and converted into a zero-value `OP_RETURN` output before the transaction is measured. [3](#0-2) 

However, `calculate_weight_vbytes` has no `data` parameter and builds its measurement transaction solely from inputs, payments, and an optional change output. [4](#0-3) 

Both the initial fee calculation and the recalculation performed when change is added pass only `payments`, so the `OP_RETURN` output is excluded from both calculations. [5](#0-4) 

With change, the change amount is reduced by the underestimated fee, causing the final transaction to pay exactly `fee_per_vbyte * underestimated_vbytes` rather than `fee_per_vbyte * actual_vbytes`. [6](#0-5) 

The maximum-standard-weight check also uses the underestimated `weight`, so a transaction can pass the check and then exceed the intended bound after the omitted data output is included. [7](#0-6) 

### Impact Explanation
An attacker who can cause public transaction data to be placed in the `data` field can make signers produce a validly signed transaction with a materially lower effective fee rate than requested. [8](#0-7) 

For a maximum-size payload, the omitted `OP_RETURN` output adds approximately 92 serialized bytes, so a transaction constructed at the minimum accepted one-satoshi-per-vbyte rate can fall below the actual one-satoshi-per-vbyte relay threshold and fail to propagate or confirm normally. [9](#0-8) 

This is a transaction-availability issue affecting funds selected as inputs, and can also produce a nonstandard transaction near the standard-weight boundary. [7](#0-6) 

### Likelihood Explanation
The issue is reachable whenever a caller supplies `Some(data)` together with a change output; it does not require malformed signatures, invalid Bitcoin inputs, or control over secret key material. [10](#0-9) 

The strongest impact occurs when the requested fee rate is close to the minimum relay rate or when the transaction is already near `MAX_STANDARD_TX_WEIGHT`. [11](#0-10) [12](#0-11) 

### Recommendation
Calculate weight and virtual size from the complete output list, including any `OP_RETURN` output, in both the no-change and with-change paths. [13](#0-12) 

Alternatively, construct the exact candidate transaction before calling `calculate_weight_vbytes`, and use that transaction’s final weight and vsize for `needed_fee`, the minimum-relay check, the funds check, the change calculation, and the maximum-standard-weight check. [14](#0-13) 

### Proof of Concept
The following test shape demonstrates the discrepancy; `input` is a spendable `ReceivedOutput`, `change` is a valid script, and `sign` completes the transaction’s FROST signatures:

```rust
let fee_rate = 1;
let data = vec![0u8; 80];

let signable = SignableTransaction::new(
  vec![input],
  &[],
  Some(change),
  Some(data),
  fee_rate,
)
.unwrap();

let signed = sign(&keys, &signable);

assert_eq!(signable.fee(), signable.needed_fee());
assert!(
  signable.fee() <
    u64::try_from(signed.vsize()).unwrap() * fee_rate
);
```

The assertion succeeds because `needed_fee` is derived from a measurement transaction without the `OP_RETURN` output, while `signed.vsize()` includes it. [8](#0-7) [15](#0-14)

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

**File:** networks/bitcoin/src/wallet/send.rs (L171-255)
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
      }
    }

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }

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
