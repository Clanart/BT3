### Title
Malformed payment amount triggers arithmetic-overflow denial of service - (networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` adds attacker-influenced payment amounts and fees using unchecked `u64` arithmetic before validating affordability. A payment amount of `u64::MAX` causes `payment_sat + needed_fee` to overflow and panic in builds with arithmetic overflow checks enabled. [1](#0-0) 

### Finding Description
The function accepts payment amounts as raw `u64` values in the `payments` slice. [2](#0-1) 

It sums those values into `payment_sat`, calculates `needed_fee`, and then evaluates `input_sat < (payment_sat + needed_fee)`. [3](#0-2) 

For `payment_sat = u64::MAX` and any nonzero `needed_fee`, that addition overflows before the intended `NotEnoughFunds` error can be returned. [4](#0-3) 

The same input class can also overflow while summing multiple attacker-controlled payment amounts. [5](#0-4) 

### Impact Explanation
An unprivileged party who can submit a requested Bitcoin payment for threshold signing can cause the transaction-construction path to panic instead of cleanly rejecting the request as unaffordable. [2](#0-1) 

This is a partial denial of service against the signer or transaction-preparation component handling that request; it does not produce a valid transaction, forged signature, or key disclosure. [6](#0-5) 

### Likelihood Explanation
The trigger requires only a payment request with a large raw `u64` amount and does not require control of a validator, node, RPC server, or threshold participant. [2](#0-1) 

The panic requires the fee calculation to produce a nonzero `needed_fee`, which is expected whenever `fee_per_vbyte` and the transaction’s virtual size are nonzero. [7](#0-6) 

### Recommendation
Use `checked_add` and checked accumulation for all payment, input, output, and fee totals. [3](#0-2) 

Return `TransactionError::NotEnoughFunds` or a dedicated invalid-amount error when arithmetic overflows, rather than relying on unchecked addition. [8](#0-7) 

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs

let payment = (p2tr_script_buf(attacker_key).unwrap(), u64::MAX);

// `input_sat` is derived from any otherwise valid received output.
// `payment_sat + needed_fee` overflows before `NotEnoughFunds` is returned.
let result = SignableTransaction::new(
  vec![received_output],
  &[payment],
  None,
  None,
  1,
);
```

With overflow checks enabled, execution panics while evaluating `payment_sat + needed_fee`; without overflow checks, the addition wraps and can incorrectly pass the affordability comparison. [4](#0-3)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L34-49)
```rust
#[derive(Clone, PartialEq, Eq, Debug, Error)]
pub enum TransactionError {
  #[error("no inputs were specified")]
  NoInputs,
  #[error("no outputs were created")]
  NoOutputs,
  #[error("a specified payment's amount was less than bitcoin's required minimum")]
  DustPayment,
  #[error("too much data was specified")]
  TooMuchData,
  #[error("fee was too low to pass the default minimum fee rate")]
  TooLowFee,
  #[error("not enough funds for these payments")]
  NotEnoughFunds { inputs: u64, payments: u64, fee: u64 },
  #[error("transaction was too large")]
  TooLargeTransaction,
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

**File:** networks/bitcoin/src/wallet/send.rs (L187-221)
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

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```
