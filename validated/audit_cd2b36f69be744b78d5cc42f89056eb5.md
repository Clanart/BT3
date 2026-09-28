### Title
`SignableTransaction` computes the fee from a transaction template that omits the `OP_RETURN` output, underpaying the fee (and under-checking weight) for any data-carrying spend - ([File: networks/bitcoin/src/wallet/send.rs](https://github.com/blackvul/serai--010))

### Summary
Analogous to the Sublime `LenderPool` bug — where a fee deducted at `start()` made the accounting denominator (`totalSupply` / `borrowLimit`) diverge from the actually withdrawable amount so withdrawals were computed against the wrong total — `SignableTransaction::new` computes `needed_fee` and the change amount from a template transaction that does not include the `OP_RETURN` output it unconditionally appends to `tx_outs`. The fee "charged" is therefore measured against a smaller transaction than the one actually signed, while the outputs (denominator) reflect the full transaction.

### Finding Description
In `SignableTransaction::new`, the `OP_RETURN` output is pushed into `tx_outs` *before* the fee/weight calculation, but `calculate_weight_vbytes` is only given `payments` — it has no knowledge of the data output:

```rust
// networks/bitcoin/src/wallet/send.rs
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
``` [1](#0-0) 

`calculate_weight_vbytes` builds the template transaction's outputs solely from `payments` (plus an optional change output), so an up-to-80-byte `OP_RETURN` output (≈90+ serialized bytes, all non-witness) is entirely absent from `weight`/`vbytes` [2](#0-1) . The same omission occurs in the change branch: `fee_with_change` is computed from a template with `payments` + change but still no `OP_RETURN` [3](#0-2) , and the final `MAX_STANDARD_TX_WEIGHT` check also uses the understated `weight` [4](#0-3) .

The actual fee paid is `sum(inputs) − sum(outputs)` [5](#0-4) . Since the `OP_RETURN` is zero-valued, the absolute fee equals `needed_fee`, but the real transaction is ~90 vbytes larger than what was priced, so the effective fee rate is strictly below the caller-specified `fee_per_vbyte`. Likewise, the minimum-relay-fee guard `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` is evaluated against the understated `vbytes` [6](#0-5) .

### Impact Explanation
Any caller that specifies `data` produces a signed transaction paying less than the intended fee rate:

- If `fee_per_vbyte` is at or near the minimum relay rate, the real fee can fall below `DEFAULT_MIN_RELAY_TX_FEE * actual_vsize`, making the fully-signed transaction non-standard/unrelayable — the multisig's funds are locked in an output the network won't propagate until a replacement is signed.
- A transaction near the size limit can exceed `MAX_STANDARD_TX_WEIGHT` once the `OP_RETURN` weight is counted, again producing an unrelayable signed transaction despite the `TooLargeTransaction` guard passing.

This is the same shape as H-01: a fee-related adjustment applied to one side of the accounting (actual outputs/actual size) but not to the quantity used for pricing/limits, producing a signed artifact that violates the invariant the code intended to enforce.

### Likelihood Explanation
The flaw triggers deterministically whenever `SignableTransaction::new` is called with `data: Some(_)` — an unprivileged party who can cause a data-carrying Bitcoin transaction to be signed reaches it with purely public inputs (the `data` bytes and `fee_per_vbyte`). Whether it becomes exploitable (unrelayable TX) depends on the chosen fee rate and transaction size, so the consistent outcome is a fee-rate discrepancy, with non-relay/oversize failure at the boundary. The processor never passes `data` today, which lowers the practical likelihood for the current in-tree caller.

### Recommendation
Pass the full output set — including the `OP_RETURN` output — into `calculate_weight_vbytes`, or add the serialized size of the `OP_RETURN` output to `weight`/`vbytes` before computing `needed_fee`. Concretely, build `tx_outs` first and call `Self::calculate_weight_vbytes(tx_ins.len(), &all_outputs_including_op_return, change)`; the `TooLowFee` and `MAX_STANDARD_TX_WEIGHT` checks must also use the OP_RETURN-inclusive `vbytes`/`weight`.

### Proof of Concept
1. Construct `SignableTransaction::new(inputs, payments, change, Some(vec![0u8; 80]), fee_per_vbyte)` where `fee_per_vbyte` equals the minimum relay rate (e.g., 1 sat/vbyte) and `input_sat` comfortably covers `payment_sat + needed_fee`.
2. `needed_fee = 1 * vbytes`, where `vbytes` excludes the ~90-byte `OP_RETURN` output.
3. The signed transaction's real vsize is `vbytes + ~90`; its fee (`sum(inputs) − sum(outputs)`) equals `needed_fee`, so its effective rate is `needed_fee / (vbytes + 90) < 1 sat/vbyte` and `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * actual_vsize / 1000`.
4. The transaction passes all internal checks (`TooLowFee` evaluated against understated `vbytes`) yet is rejected by Bitcoin Core's mempool minimum-fee filter — a fully FROST-signed transaction that cannot be relayed, mirroring H-01's withdrawal that is computed valid but fails against the actual available amount.

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

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
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

**File:** networks/bitcoin/src/wallet/send.rs (L224-233)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
