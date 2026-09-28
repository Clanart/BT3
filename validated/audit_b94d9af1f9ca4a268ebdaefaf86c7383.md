### Title
OP_RETURN data output is omitted from fee and change accounting - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` accepts a caller-supplied `data` field and adds it as an OP_RETURN transaction output, but `calculate_weight_vbytes` only models payment outputs and an optional change output. The missing output causes both `needed_fee` and the change amount to be computed from an undersized transaction, so the signed transaction pays a lower fee rate than requested and can fall below Bitcoin’s minimum relay fee.

### Finding Description
`SignableTransaction::new` appends the OP_RETURN output to `tx_outs` before calculating weight and fees. However, the initial estimate calls `calculate_weight_vbytes(tx_ins.len(), payments, None)`, and the helper’s synthetic transaction contains only `payments` plus optional change; it has no `data` parameter and never constructs the OP_RETURN output. [1](#0-0) [2](#0-1) 

The same omission occurs when change is considered: `fee_with_change` is calculated from inputs, payments, and change only, again excluding the already-appended data output. [3](#0-2) 

The resulting object commits to the real transaction containing the OP_RETURN output, while `needed_fee` retains the estimate for the smaller transaction without it. [4](#0-3) 

### Impact Explanation
For a transaction with change, the change output receives `input_sat - payment_sat - underestimated_fee`, rather than subtracting the fee required for the larger transaction that includes OP_RETURN. The actual fee remains `underestimated_fee`, reducing the effective sat/vbyte rate. With an 80-byte payload, the omitted output contributes roughly 90 serialized bytes, so a nominal 1 sat/vbyte transaction can be produced below the default minimum relay rate. The constructor’s minimum-fee check also uses the underestimated `vbytes`, so it does not detect this condition. [5](#0-4) [3](#0-2) 

An untrusted party that can cause a nonempty `data` payload to be included can therefore cause validators/signers to produce a validly signed but economically mispriced transaction that may not relay or confirm at the intended rate. The transaction signs the constructed transaction through Taproot sighashes over all inputs, so the signature itself is valid; the accounting input used to construct it is wrong. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
The bug is deterministically reachable whenever `SignableTransaction::new` is called with `Some(data)` and a change output. `data` is a public byte-vector argument, with up to 80 bytes accepted before the OP_RETURN output is appended. [8](#0-7) [9](#0-8) 

The current processor call site observed in the repository passes `None` for `data`, which limits the demonstrated production integration path, but the vulnerable constructor is public in the in-scope Bitcoin wallet API and any integration that uses the documented OP_RETURN feature reaches it directly. [10](#0-9) 

### Recommendation
Include the serialized OP_RETURN output in the transaction model used by `calculate_weight_vbytes`, or change that helper to accept the complete output list—including data and optional change—rather than reconstructing only payments and change. Recalculate the actual transaction weight after constructing all outputs and add a regression test asserting `needed_fee == fee_per_vbyte * transaction_vsize` and `fee() == needed_fee()` when both `data` and `change` are present.

### Proof of Concept
1. Construct a `SignableTransaction` with one spendable input, one payment, `Some(change)`, and `Some(vec![0; 80])`.
2. The constructor adds a third OP_RETURN output before estimating fees, but both calls to `calculate_weight_vbytes` omit that output.
3. Compare `needed_fee()` with `fee()`: `needed_fee()` is based on the smaller payment-only/payment-plus-change transaction, while the serialized transaction that gets signed includes the additional OP_RETURN output.
4. Consequently, `tx.output` contains change calculated as though the OP_RETURN output had zero weight, while the real signed transaction carries that output and has a lower effective fee rate. [11](#0-10)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-99)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L150-155)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
```

**File:** networks/bitcoin/src/wallet/send.rs (L171-232)
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

**File:** networks/bitcoin/src/wallet/send.rs (L417-427)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }

    Ok(self.tx)
```

**File:** processor/src/networks/bitcoin.rs (L446-452)
```rust
    match BSignableTransaction::new(
      inputs.iter().map(|input| input.output.clone()).collect(),
      &payments,
      change.clone().map(Into::into),
      None,
      fee.0,
    ) {
```
