### Title
Fee and minimum-relay checks computed on a transaction smaller than the one actually created when an OP_RETURN `data` output is present - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` mirrors the reported bug class: the value used to validate/account for the transaction (the weight/vbytes-derived `needed_fee`) is computed on a different, smaller set of outputs than the transaction that is actually built and signed. The OP_RETURN output carrying `data` is appended to `tx_outs` (send.rs:194-202) but `calculate_weight_vbytes` is called with `payments` — not `tx_outs` — at send.rs:204 and again at send.rs:226, so the data output is never counted in the size used for fee accounting.

### Finding Description
Just as `_generateDebt` records the modified `_deltaDebt` in the SAFE but mints coins for the original `_deltaWad`, `SignableTransaction::new` computes `vbytes`, `needed_fee`, the `TooLowFee` minimum-relay check (send.rs:211), and the `NotEnoughFunds` check (send.rs:215) on a transaction that excludes the OP_RETURN output which is unconditionally included in `tx.output` (send.rs:246-251). The change-output branch has the same defect: `fee_with_change` (send.rs:227) is derived from `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`, which again omits the data output, and the change amount is then set to `input_sat - payment_sat - fee_with_change` (send.rs:228-230) — an over-credited change value that further depresses the real fee. The signed transaction therefore carries an extra ~10-90 vbytes (up to 80 bytes of data plus output overhead) whose fee was never paid. Since `needed_fee` only had to clear the minimum relay check under the *understated* size, the real fee rate `actual_fee / real_vbytes` can fall below `DEFAULT_MIN_RELAY_TX_FEE` even though the check at send.rs:211 passed.

### Impact Explanation
A `SignableTransaction` created with `data` can be constructed, signed by `TransactionMachine`, and broadcast while paying an effective fee rate below the Bitcoin minimum relay fee (and below the caller-specified `fee_per_vbyte`). Such a transaction will not propagate through the default relay network, so the payments and change it carries are effectively unspendable/stuck until reconstructed — the analog of minted tokens being insufficient to cover the recorded debt. Additionally, the change output is credited `real_data_vbytes * fee_per_vbyte` more satoshis than the declared fee rate warrants, and `needed_fee()`/`fee()` report inconsistent values for callers that reconcile against the computed fee.

### Likelihood Explanation
Any caller of the public `SignableTransaction::new` API that supplies `data` (up to 80 bytes, send.rs:171-173) with a `fee_per_vbyte` near the minimum relay rate deterministically produces an under-priced transaction — no attacker sophistication is required, just a caller-supplied OP_RETURN payload, which is a normal public input to this API. The miscount is deterministic whenever `data.is_some()`.

### Recommendation
Compute the weight/vbytes on the actual output set. Change `calculate_weight_vbytes` to accept `&[TxOut]` (or pass `&tx_outs` including the OP_RETURN output) so that send.rs:204 and send.rs:226 measure the same transaction that is stored in `SignableTransaction.tx`. Equivalently, append the data output after the fee math is finalized — the invariant to restore is that the outputs used for fee calculation equal the outputs committed into `tx`.

### Proof of Concept
From `networks/bitcoin/src/wallet/send.rs`:

```rust
// OP_RETURN output added to tx_outs (lines 194-202)
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

// ...but weight/fee computed on `payments`, excluding tx_outs/data (line 204)
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;   // passes against understated size
}

// Same omission in the change branch (lines 225-232)
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
let fee_with_change = fee_per_vbyte * vbytes_with_change;
// change credited with input_sat - payment_sat - fee_with_change (line 230)

// Final tx includes the data output the fee never paid for (lines 246-251)
```

Construct `SignableTransaction::new(inputs, payments, Some(change), Some(vec![0; 80]), 1)` where `needed_fee` equals exactly `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` for the data-less vbytes. The resulting `tx` contains an additional ~95-weight-unit OP_RETURN output; `tx.fee() / real_vsize < 1 sat/vbyte`, below the relay minimum, while `needed_fee()` reports the passed value — the recorded/accounted amount and the actually committed amount diverge, exactly the M-04 pattern. [1](#0-0)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-234)
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
```
