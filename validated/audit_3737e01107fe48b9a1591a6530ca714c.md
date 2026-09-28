### Title
Unvalidated Bitcoin payment amounts cause arithmetic-overflow denial of service - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` sums attacker-controlled payment amounts with `Iterator::sum::<u64>()`. Two otherwise dust-valid payments totaling more than `u64::MAX` overflow before the transaction is validated or returned as an error. The workspace enables integer overflow checks even in release builds, making this a repeatable panic rather than silent wrapping. [1](#0-0) [2](#0-1) 

### Finding Description
The constructor accepts arbitrary `payments: &[(ScriptBuf, u64)]`. It only rejects amounts below `DUST`; it does not reject amounts above Bitcoin's maximum money supply or bound their aggregate. It then computes `payments.iter().map(|payment| payment.1).sum::<u64>()`. Supplying two payments with values `u64::MAX` and `DUST` causes an integer-overflow panic during transaction construction. [3](#0-2) 

The same unchecked arithmetic is used for `payment_sat + needed_fee` and `payment_sat + fee_with_change`, so even an accepted `payment_sat` near `u64::MAX` can panic later. [4](#0-3) 

### Impact Explanation
A caller capable of causing Serai to construct a transaction with arbitrary payment values can repeatedly crash the constructing task or process instead of receiving a `TransactionError`. This is a remote-input-triggerable denial-of-service analog to the referenced availability vulnerability: the malformed input is not merely invalid; it reaches unchecked arithmetic and terminates execution before normal validation can reject it. [1](#0-0) 

### Likelihood Explanation
The panic requires only a non-empty `ReceivedOutput` list and two payment entries with sufficiently large `u64` values. No malformed curve encoding, invalid signature, consensus rule violation, private key, or privileged blockchain state is required. Exploitability depends on whether payment requests reaching this public constructor are constrained upstream to amounts backed by reserves; the in-scope wallet API itself does not enforce that bound. [5](#0-4) 

### Recommendation
Validate each payment and the aggregate payment total with checked arithmetic before constructing the transaction. Reject individual outputs above `Amount::MAX_MONEY.to_sat()` and return a new `TransactionError::InvalidPayment` or `TransactionError::PaymentOverflow` for aggregate overflow. Also use `checked_mul` for `fee_per_vbyte * vbytes` and `checked_add` for all fee/output totals. [6](#0-5) 

### Proof of Concept
A minimal call needs one valid `ReceivedOutput` and two dust-valid payment amounts whose sum exceeds `u64::MAX`:

```rust
// networks/bitcoin/src/wallet/send.rs
let script = ScriptBuf::new_p2wsh(Default::default());
let inputs = vec![received_output]; // Any syntactically valid received output.

let result = SignableTransaction::new(
  inputs,
  &[(script.clone(), u64::MAX), (script, DUST)],
  None,
  None,
  1,
);
```

Execution reaches:

```rust
// networks/bitcoin/src/wallet/send.rs:187
let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

Because `u64::MAX + DUST > u64::MAX` and release builds enable `overflow-checks`, this aborts with an arithmetic-overflow panic before `NotEnoughFunds` can be returned. [7](#0-6) [2](#0-1)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L150-228)
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
```

**File:** Cargo.toml (L116-118)
```text
[profile.release]
panic = "unwind"
overflow-checks = true
```
