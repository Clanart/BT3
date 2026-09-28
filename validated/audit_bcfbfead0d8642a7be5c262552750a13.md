### Title
OP_RETURN `data` output omitted from fee/weight calculation causes under-fee and wrong change, yielding an unrelayable transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an OP_RETURN output carrying the caller-supplied `data` to `tx_outs`, but computes the transaction weight/vbytes — and therefore `needed_fee` and the change amount — using only `payments` (and optionally `change`). The OP_RETURN output's size is never included in the fee calculation, so the constructed transaction underpays relative to the requested `fee_per_vbyte` (or the change output is inflated), and the minimum-relay-fee check is evaluated against a vbyte count that excludes the data output.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed to `tx_outs` before weight calculation:

```rust
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
``` [1](#0-0) 

`calculate_weight_vbytes` builds the weight-estimation transaction only from `payments` and an optional change script — the already-appended OP_RETURN output is never represented: [2](#0-1) 

The minimum relay fee check then uses this understated `vbytes`: [3](#0-2) 

Finally, when a change address is present, the change value is computed as `input_sat - payment_sat - fee_with_change`, where `fee_with_change` was again computed without the data output: [4](#0-3) 

This is the same bug class as the reference report: a value (the exchange/output target) is conditioned on a parameter (`_podSwapAmtOutMin` / `data`), while the residual amount (`_borrowAmtRemaining` / change-and-fee residual) is computed under the assumption that the parameter had no effect — leaving the actual balance-sheet of the operation inconsistent.

### Impact Explanation
Two concrete consequences for a signed transaction produced with `data` set:

1. **Under-fee / stuck transaction.** The `TooLowFee` guard and `needed_fee` are evaluated on a vbyte count that excludes the OP_RETURN output (up to ~90+ serialized bytes). When `change` is `None`, the actual fee paid equals `needed_fee` but the real vsize is larger, so the effective fee rate is below `fee_per_vbyte` and can fall under `DEFAULT_MIN_RELAY_TX_FEE` — nodes reject the transaction and the funds in the consumed inputs are unspendable until a replacement is signed. This matches the report's "leftover/stuck tokens" outcome.

2. **Misallocated residual.** When `change` is `Some`, the change output silently absorbs the missing fee (`input_sat - payment_sat - fee_with_change`), so the actual fee stays `fee_with_change` while the real transaction is larger — again an effective fee rate below the caller-specified bound, with the deficit hidden inside change rather than reported.

### Likelihood Explanation
`data` is a caller-controlled parameter of the public `SignableTransaction::new` API (`Option<Vec<u8>>`, ≤ 80 bytes). Any party constructing a Serai bitcoin transaction with an OP_RETURN payload hits this deterministically — no adversarial timing or privileged access needed. The internal processor currently passes `None`, so the defect only manifests for integrators/users of the wallet API who attach data.

### Recommendation
Include the OP_RETURN output (or an equivalent-size placeholder script) in the transaction used by `calculate_weight_vbytes`, so `vbytes`, `needed_fee`, the `TooLowFee` check, and the change subtraction all account for the data output. Alternatively, build `tx_outs` first and derive weight directly from the final output set.

### Proof of Concept
1. Call `SignableTransaction::new(inputs, payments, Some(change), Some(vec![0xAA; 80]), fee_per_vbyte)` where `fee_per_vbyte` is chosen so `fee_per_vbyte * vbytes` just exceeds `(DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` for the computed (data-excluded) `vbytes`.
2. With `change = None`: the resulting `Transaction` has an extra ~93-byte OP_RETURN output; its real vsize is larger, so its effective fee rate is below `fee_per_vbyte` and can fall below the 1 sat/vbyte relay minimum — `send_raw_transaction` rejects it despite `TooLowFee` not firing.
3. With `change = Some(...)`: the change output equals `input_sat - payment_sat - fee_with_change`; the actual fee (`tx.fee()`) remains `fee_with_change`, but the transaction is ~93 bytes larger than what `fee_with_change` priced, again producing an effective rate below the specified `fee_per_vbyte`.

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

**File:** networks/bitcoin/src/wallet/send.rs (L194-206)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L211-213)
```rust
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-234)
```rust
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
