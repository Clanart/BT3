### Title
Fee and change amounts computed from a transaction that omits the OP_RETURN data output, producing an underfunded transaction - ([File: networks/bitcoin/src/wallet/send.rs](https://github.com/Annirich/serai--025/blob/main/networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the Tempus `lend` bug — where the minted amount was derived from a quantity unrelated to what was actually received — `SignableTransaction::new` derives the transaction's `weight`, `vbytes`, `needed_fee`, and the change amount from a synthetic transaction built **only from `payments`**, while the real transaction also includes a caller-controlled OP_RETURN `data` output. The fee is therefore calculated against a different (smaller) transaction than the one actually signed.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` before the weight is measured: [1](#0-0) 

but `calculate_weight_vbytes` is called with `payments` — which does **not** include the data output: [2](#0-1) 

The same omission exists in the change path, so `fee_with_change` and the change `value` are also computed against a transaction lacking the data output: [3](#0-2) 

`calculate_weight_vbytes` itself only builds outputs from `payments` plus optional change, confirming the data output contributes zero measured weight: [4](#0-3) 

Additionally, the minimum-relay-fee check uses the same underestimated `vbytes`, so it can pass for a transaction whose *real* vsize would fail it: [5](#0-4) 

### Impact Explanation
The actual signed transaction is larger than what the fee was computed for, so the effective fee rate is below `fee_per_vbyte`, and can fall below `DEFAULT_MIN_RELAY_TX_FEE` for the real vsize. Since the transaction uses `Sequence::MAX` (no RBF) and a fixed output set, an under-fee transaction can be rejected by relays/mempool and never confirm — leaving the scanned `ReceivedOutput`s (including forwarded bridge outputs whose instruction data populated `data`) locked in a stuck transaction. This is "funds reported received that are not spendable" — the direct analog of users being minted a wrong/zero amount based on an unrelated quantity.

### Likelihood Explanation
`data` is attacker-influenced: bridge instructions embed arbitrary user data (capped at 80 bytes) as the OP_RETURN output, so any deposit/burn instruction carrying data triggers the miscalculation deterministically. Whether relay failure occurs depends on the chosen `fee_per_vbyte`; the effective-rate reduction (~89+ extra vbytes unaccounted) applies in every case, so overpayment-free confirmation is not guaranteed even when the tx does relay.

### Recommendation
Include the OP_RETURN data output in the transaction template used by `calculate_weight_vbytes` (e.g., pass the already-built `tx_outs` including the data output), and re-derive `vbytes` for the `TooLowFee` and `NotEnoughFunds` checks from the *complete* output set.

### Proof of Concept
1. Call `SignableTransaction::new` with any inputs, a valid payment, `change: Some(...)`, and `data: Some(vec![0; 80])`.
2. The returned `needed_fee` equals `fee_per_vbyte * vbytes` where `vbytes` was measured on a transaction without the ~89-vbyte OP_RETURN output.
3. `tx.transaction()` contains the extra output, so `fee() / tx.vsize() < fee_per_vbyte`; for marginal `fee_per_vbyte` the signed transaction is under `DEFAULT_MIN_RELAY_TX_FEE` for its true vsize and cannot enter the mempool, permanently freezing its inputs absent manual reconstruction.

Note: I was unable to fully trace the `prepare_send`/processor-side fee buffer (e.g., whether callers inflate `fee_per_vbyte` to compensate); if a systematic margin exists upstream, the practical impact reduces to an incorrect fee rate rather than relay failure.

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

**File:** networks/bitcoin/src/wallet/send.rs (L204-206)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L211-213)
```rust
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L225-234)
```rust
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
