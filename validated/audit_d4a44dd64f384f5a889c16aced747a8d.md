### Title
`SignableTransaction::new` checks the maximum transaction weight before accounting for the `OP_RETURN` output - (`networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` adds an attacker-controlled `data` output to the transaction before estimating its weight, but calls `calculate_weight_vbytes` with only `payments`, omitting the `OP_RETURN` output from both the fee and maximum-weight calculations. As a result, a transaction exceeding `MAX_STANDARD_TX_WEIGHT` can pass the final size check and proceed to threshold signing.

### Finding Description
The constructor appends an `OP_RETURN` output to `tx_outs` when `data` is supplied. However, the subsequent call to `calculate_weight_vbytes` passes `payments` rather than the complete output list, and the same omission occurs when calculating the fee and weight with a change output. [1](#0-0) [2](#0-1) 

`calculate_weight_vbytes` accurately includes every payment script it receives, including attacker-selected script lengths, but it has no parameter representing the `data` output. [3](#0-2) 

The constructor compares only the underestimated model weight against `MAX_STANDARD_TX_WEIGHT`, then stores the transaction containing the omitted `OP_RETURN` output. [4](#0-3) 

### Impact Explanation
An unprivileged caller can cause the wallet to construct and sign a transaction whose actual weight exceeds the standardness limit enforced by the constructor. The Taproot sighash and signature generation operate on `self.tx.tx`, so the resulting signature commits to the oversized transaction that the bound failed to reject. [5](#0-4) 

When a change output is used, the omitted `OP_RETURN` output also causes `needed_fee` to be lower than the configured fee rate for the actual transaction size. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
The caller controls `data`, and the code explicitly permits up to 80 bytes. The caller also controls payment script lengths, allowing the estimated transaction weight to be placed immediately below the maximum so that adding the unaccounted `OP_RETURN` output pushes the actual transaction above it. [8](#0-7) [9](#0-8) 

### Recommendation
Calculate weight and virtual size from the complete set of transaction outputs, including the `OP_RETURN` output, in both the no-change and change cases. A clean fix is to change `calculate_weight_vbytes` to accept the already-built `tx_outs` list rather than separately reconstructing outputs from `payments`. [10](#0-9) 

### Proof of Concept
A public-input trigger is:

1. Create a funded `ReceivedOutput`.
2. Supply payment `ScriptBuf`s padded so `calculate_weight_vbytes(1, payments, None)` returns a weight at or just below `MAX_STANDARD_TX_WEIGHT`.
3. Supply `data = Some(vec![0; 80])`.
4. Call `SignableTransaction::new`.

The `data` output is added to `tx_outs`, but the weight model remains based only on `payments`; therefore the constructor returns `Ok` even though the returned `Transaction` contains an extra `OP_RETURN` output and weighs more than the checked model. [11](#0-10) [12](#0-11)

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

**File:** networks/bitcoin/src/wallet/send.rs (L171-173)
```rust
    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-212)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-234)
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
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-255)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
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
        )?;
```
