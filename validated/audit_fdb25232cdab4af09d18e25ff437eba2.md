### Title
Attacker-controlled payment totals can overflow and cause signing of an invalid Bitcoin transaction - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` sums all requested output amounts into `payment_sat` using unchecked `u64` addition and then checks affordability with `payment_sat + needed_fee`. A request containing output values whose total exceeds `u64::MAX` causes this arithmetic to wrap in release builds, allowing a consensus-invalid transaction to pass the funds check and proceed to signing.

### Finding Description
`payment_sat` is calculated with `payments.iter().map(|payment| payment.1).sum::<u64>()` without checking for overflow. The subsequent affordability test also uses unchecked `payment_sat + needed_fee`. [1](#0-0) 

Because each payment only needs to satisfy the dust check, an attacker can provide payment amounts near `u64::MAX`. [2](#0-1) 

The overflowed sum can become small enough that `input_sat < (payment_sat + needed_fee)` is false, even though the requested payments are far larger than all available inputs. [3](#0-2) 

The oversized outputs are inserted into the transaction before affordability is checked, so the function can return a `SignableTransaction` containing outputs with impossible values. [4](#0-3) 

That transaction is later hashed and passed to the threshold Schnorr signing machines, meaning an attacker-controlled transaction template can obtain threshold signatures for a transaction that could never have been validly funded. [5](#0-4) 

### Impact Explanation
An unprivileged requester who can cause a Bitcoin transaction to be constructed and signed can force the signing subsystem to authorize a malformed transaction with output amounts that exceed the supplied inputs. This is a concrete signing of an unintended message; it can also trigger denial of service through arithmetic panics in overflow-checked builds or through `fee()`'s unchecked subtraction when the invalid object is inspected. [6](#0-5) 

### Likelihood Explanation
The trigger requires only attacker-selected payment values and does not require control of a validator, leaked keys, malformed curve points, or a malicious Bitcoin RPC response. The attack requires enough aggregate `u64` output value to wrap, which is easy to specify through the public transaction-construction API, although it does not produce a spendable Bitcoin transaction because consensus validation will reject the impossible outputs. [7](#0-6) 

### Recommendation
Use `checked_add` or `checked_sum` for `payment_sat`, `input_sat`, `needed_fee`, and all fee/output comparisons. Reject totals exceeding `u64::MAX` and preferably enforce Bitcoin's maximum-money limit before constructing any `TxOut`. `fee()` should also use `checked_sub` and return an error rather than relying on construction-time invariants. [8](#0-7) 

### Proof of Concept
Conceptually, call `SignableTransaction::new` with one legitimate received input and one payment whose amount is `u64::MAX`, using a fee rate which leaves `needed_fee` nonzero: [7](#0-6) 

```rust
// networks/bitcoin/src/wallet/send.rs
let tx = SignableTransaction::new(
  vec![received_output],
  &[(payment_script, u64::MAX)],
  None,
  None,
  1,
);
```

In a release build, `u64::MAX + needed_fee` wraps to a small value, so the affordability check does not reject the request. The returned transaction contains an output worth `u64::MAX`, is passed to `taproot_key_spend_signature_hash` during signing, and may later panic in `fee()` when subtracting the output total from the input total. [9](#0-8)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
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

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-228)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
```rust
    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
```
