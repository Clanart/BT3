### Title
OP_RETURN data bypasses transaction weight and fee limits - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` adds an OP_RETURN output before calculating the transaction’s weight, virtual size, required fee, and maximum-standard-weight eligibility, but its size calculation only receives `payments` and does not include the OP_RETURN output. An unprivileged caller can therefore use the public `data` argument to create a transaction which passes `TooMuchData` and `TooLargeTransaction` checks while exceeding `MAX_STANDARD_TX_WEIGHT` and paying less than the requested fee rate.

### Finding Description
The constructor enforces an 80-byte limit on `data`, then appends an OP_RETURN output containing it [1](#0-0) . However, the subsequent call to `calculate_weight_vbytes` passes only the input count, `payments`, and `None` for change; it does not pass or otherwise account for `tx_outs`, which already contains the OP_RETURN output [2](#0-1) .

The omitted output affects both the transaction’s actual serialized size and its fee requirement. The same omission occurs in the change calculation, which again calls `calculate_weight_vbytes` with `payments` rather than the already-created OP_RETURN output [3](#0-2) . Finally, `MAX_STANDARD_TX_WEIGHT` is enforced against the incorrectly calculated `weight`, not the actual transaction weight [4](#0-3) .

This is analogous to a limit checked on one path while another value-carrying path bypasses it: the 80-byte OP_RETURN is accepted as a transaction output, yet is excluded from the size and fee accounting that enforce the transaction-level limits.

### Impact Explanation
A caller can produce a `SignableTransaction` that exceeds Bitcoin’s standard transaction weight even though `SignableTransaction::new` explicitly claims to reject oversized transactions. Threshold participants can then sign the transaction normally because `TransactionSignMachine::sign` derives sighashes from the final transaction object [5](#0-4) . The resulting signed transaction may fail standard relay/policy checks and pay a lower effective fee rate than requested. The spend must be reconstructed and signed again with different outputs, preventing the signed transaction from achieving its intended payment.

### Likelihood Explanation
The issue is directly reachable through the public `data: Option<Vec<u8>>` argument to `SignableTransaction::new`. Any non-empty OP_RETURN increases the real transaction size without increasing the calculated `weight` or `vbytes`. The bypass is deterministic whenever the correctly measured transaction would cross the maximum weight boundary or when the omitted output materially lowers the achieved fee rate.

### Recommendation
Build the complete output set—including payments, OP_RETURN, and potential change—before calculating weight, or change `calculate_weight_vbytes` to accept the actual `Vec<TxOut>` being considered. Recalculate `needed_fee` from the virtual size including OP_RETURN, and apply `MAX_STANDARD_TX_WEIGHT` to the final transaction’s actual weight.

### Proof of Concept
1. Call `SignableTransaction::new` with:
   - one or more valid `ReceivedOutput` inputs,
   - payment outputs whose calculated size is just below `MAX_STANDARD_TX_WEIGHT`,
   - `data = Some(vec![0; 80])`, and
   - a nonzero `fee_per_vbyte`.
2. The 80-byte data value passes the `TooMuchData` check and is appended as an OP_RETURN output.
3. `calculate_weight_vbytes` constructs a transaction containing only the payment outputs; it omits the OP_RETURN output.
4. The resulting `weight` and `needed_fee` are therefore lower than those of the actual transaction stored in `SignableTransaction`.
5. If the payment set was selected so the omitted OP_RETURN pushes the transaction above `MAX_STANDARD_TX_WEIGHT`, the final `TooLargeTransaction` check still passes because it uses the undercounted `weight`.
6. `multisig` and the FROST transaction machines sign the final transaction including the OP_RETURN output, yielding a fully signed transaction whose actual weight violates the invariant checked by the constructor.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L171-201)
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

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
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
