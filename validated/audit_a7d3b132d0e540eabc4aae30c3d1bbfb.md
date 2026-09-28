### Title
OP_RETURN data is omitted from transaction fee and weight accounting, allowing an underfunded or oversized transaction to be signed - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` appends an OP_RETURN output supplied through `data`, but calculates virtual size, required fee, and maximum transaction weight using only `payments`. An attacker who can influence the `data` argument can cause the resulting transaction to be accepted by the constructor and signed despite not satisfying the requested fee rate or the standard transaction-weight limit.

### Finding Description
The constructor adds the OP_RETURN output to `tx_outs` at lines 193-202. It then calls `calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204, passing only `payments`; the data output is not represented in the transaction used for the size calculation. [1](#0-0) 

`needed_fee`, the minimum-relay check, the available-inputs check, and the later `MAX_STANDARD_TX_WEIGHT` check are therefore all performed against a smaller transaction than the one ultimately placed in `SignableTransaction`. [2](#0-1) [3](#0-2) 

The resulting transaction is subsequently signed through `TransactionSignMachine::sign`, which hashes the complete transaction—including the omitted-from-accounting OP_RETURN output—with `taproot_key_spend_signature_hash`. [4](#0-3) 

### Impact Explanation
A transaction can be produced and threshold-signed while paying less than the caller-requested `fee_per_vbyte`, potentially below Bitcoin's minimum relay fee, or while exceeding the standard transaction-weight limit. The signed transaction can consequently be rejected by the Bitcoin network even though `SignableTransaction::new` returned success and the signing protocol consumed a signing round for it.

This is a concrete authorization failure: the group signs transaction bytes represented as satisfying a fee and size policy that they do not actually satisfy.

### Likelihood Explanation
The bug is deterministic whenever `data` is non-empty. Up to 80 bytes are accepted, so an OP_RETURN output can add roughly enough serialized data to materially reduce the effective feerate or push a borderline transaction over the maximum standard weight. The attacker only needs to cause a non-empty attacker-controlled `data` value to reach `SignableTransaction::new`.

### Recommendation
Include the complete `tx_outs`, including any OP_RETURN output, when calculating transaction weight and virtual size. This can be done by either:

- passing the constructed `tx_outs` to `calculate_weight_vbytes`, rather than separately passing `payments`; or
- adding a representation of the OP_RETURN output to the transaction built inside `calculate_weight_vbytes`.

The same complete output list must also be used for the optional change-output size calculation and the final `MAX_STANDARD_TX_WEIGHT` check.

### Proof of Concept
Conceptually:

```rust
// fee_per_vbyte = 1 sat/vbyte
let base = SignableTransaction::new(
  vec![input],
  &[(payment_script, payment_amount)],
  None,
  None,
  1,
).unwrap();

let base_vbytes = base.needed_fee(); // fee_per_vbyte is 1
let underfunded_data_tx = SignableTransaction::new(
  vec![input],
  &[(payment_script, payment_amount)],
  None,
  Some(vec![0; 80]),
  1,
).unwrap();
```

If `input.value() == payment_amount + base_vbytes`, the first transaction pays exactly 1 sat/vbyte. The second transaction has the same reserved fee but an additional serialized OP_RETURN output, so its actual feerate is less than 1 sat/vbyte. Because the minimum-relay check also uses the omitted-output size, it does not reject the transaction. The returned object can then proceed through `multisig`, `preprocess`, `sign`, and `complete`, producing signatures for a transaction Bitcoin nodes may reject as underpriced or non-standard.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L193-215)
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
    }

    if input_sat < (payment_sat + needed_fee) {
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-254)
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
