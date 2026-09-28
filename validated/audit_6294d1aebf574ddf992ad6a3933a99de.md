### Title
`SignableTransaction` computes the fee from a weight estimate that excludes the OP_RETURN output, so the actual transaction pays a lower feerate than requested - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The referenced bug uses a nominal input amount (pre-exchange ETH) where the actual post-operation amount (sUSD received) was required. The same class exists in `SignableTransaction::new`: the fee is derived from `calculate_weight_vbytes`, which is called with `payments` only, while the final transaction also includes the OP_RETURN `data` output. The transaction that gets signed is therefore larger than the size used to price it, so it pays a lower effective feerate than the `fee_per_vbyte` the caller specified.

### Finding Description
`SignableTransaction::new` pushes the OP_RETURN output onto `tx_outs` when `data` is supplied [1](#0-0) , but both calls to `calculate_weight_vbytes` pass only `payments` (and optionally `change`), never the OP_RETURN output [2](#0-1) . `needed_fee` is then set from the underestimated `vbytes` [3](#0-2) , and the change output is computed as `input_sat - payment_sat - fee_with_change`, locking the actual fee to the underestimated value [4](#0-3) . Because the signed transaction commits to all outputs via `Prevouts::All` sighash [5](#0-4) , the real broadcast transaction is strictly larger (by the serialized size of the OP_RETURN output, up to ~89 vbytes for 80 bytes of data) while paying exactly the underestimated fee — the "amount used" (fee charged against inputs) corresponds to a different, smaller transaction than the one actually produced.

### Impact Explanation
Every transaction carrying an `data` payload pays a materially lower effective feerate than the `fee_per_vbyte` the scheduler requested. For an 80-byte payload, the fee is priced on a transaction missing ~89+ bytes of output/witness overhead, so the effective feerate can drop substantially below the target and below relay/mempool acceptance during congestion. The resulting transaction can be stuck or evicted — a liveness failure for processor outflows — and `needed_fee()` misreports the fee required for the transaction actually produced, since callers cannot recover the "correct" fee for the real size. This matches the report's impact (unpredictable results / DoS from using the wrong amount in the computation).

### Likelihood Explanation
Reachable whenever a transaction is built with `data: Some(..)`; `data` flows from untrusted in-instructions carried in external outputs scanned by the processor, so an unprivileged depositor can cause OP_RETURN-bearing transactions to be constructed. No special conditions are needed beyond `data` being non-empty.

### Recommendation
Include the OP_RETURN output in the weight/vsize estimate — e.g., build the candidate `tx_outs` (payments + OP_RETURN + change) first and pass them to `calculate_weight_vbytes`, or add the OP_RETURN output's serialized size to the computed weight before pricing the fee.

### Proof of Concept
Conceptually: `SignableTransaction::new(inputs, payments, change, Some(vec![0; 80]), fee_rate)` produces a `tx` whose `tx.weight()` exceeds the `weight`/`vbytes` used to derive `needed_fee`, while `fee() == needed_fee` (or `fee_with_change`). The effective feerate `fee() / real_vsize` is less than `fee_rate`. A differential test asserting `needed_fee >= fee_rate * real_vsize` fails whenever `data` is non-empty.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-202)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L204-233)
```rust
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
