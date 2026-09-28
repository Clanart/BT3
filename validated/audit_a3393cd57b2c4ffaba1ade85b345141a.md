### Title
`SignableTransaction::new` computes fee and weight on a transaction that omits the OP_RETURN output - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
When a `data` payload is supplied, `SignableTransaction::new` pushes an OP_RETURN `TxOut` onto `tx_outs` before estimating the transaction's weight — but the estimate in `calculate_weight_vbytes` is built solely from `payments`, never including the OP_RETURN output. Both `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check are therefore computed on a smaller transaction than the one actually signed and broadcast, analogous to applying a rate to a base that doesn't reflect the true value.

### Finding Description
`SignableTransaction::new` appends the OP_RETURN output to `tx_outs` (the outputs actually committed to the signed transaction):

```rust
// Add the OP_RETURN output
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}
``` [1](#0-0) 

It then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, where the reconstruction inside `calculate_weight_vbytes` builds outputs only from `payments` — the OP_RETURN output in `tx_outs` is absent from the estimate:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
``` [2](#0-1) [3](#0-2) 

The same stale `weight`/`vbytes` feed:
- the `TooLowFee` minimum-relay check (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`), which also uses the under-estimated `vbytes`,
- the change-branch `fee_with_change` calculation, and
- the `MAX_STANDARD_TX_WEIGHT` standardness check on `weight`, which omits the OP_RETURN's ~83+ weight units. [4](#0-3) 

This is reachable from public inputs: `data` is a caller-supplied `Option<Vec<u8>>` parameter of the public constructor, bounded only by the 80-byte `TooMuchData` check.

### Impact Explanation
The signed transaction's real vsize exceeds the estimated `vbytes`, so `fee() / actual_vsize < fee_per_vbyte` — the transaction pays a lower fee rate than requested and reported by `needed_fee()`. When `fee_per_vbyte` is at/near the minimum relay rate, the real fee rate falls below `DEFAULT_MIN_RELAY_TX_FEE` while the internal `TooLowFee` check still passes (it divides by the same under-estimated vbytes), producing a transaction that peers won't relay and which stalls the signing/plan flow. With an 80-byte payload near the weight limit, a transaction can also pass the `MAX_STANDARD_TX_WEIGHT` check while exceeding it, yielding a non-standard transaction. Like the Atlas bug — where a percentage was applied to a base that already included the percentage — here a rate is applied to a base missing a component of the thing being priced.

### Likelihood Explanation
`data` is currently always `None` in the processor's `make_signable_transaction` call, so the bug is only exercised when an integrator (or a future processor path) supplies OP_RETURN data. Within the library API it is fully reachable via untrusted input, and requires no special privileges.

### Recommendation
Include the OP_RETURN output in the weight estimate — e.g., pass the already-built `tx_outs` (or an extended `payments`-equivalent list containing the OP_RETURN output) into `calculate_weight_vbytes` instead of `payments`, for both the no-change and with-change estimates.

### Proof of Concept
Call `SignableTransaction::new` with one input, one payment, `change: None`, and `data: Some(vec![0u8; 80])`. The resulting `SignableTransaction` reports `needed_fee = fee_per_vbyte * vbytes_without_op_return`, while `fee()` (= inputs − outputs, which does include the OP_RETURN's real serialized size on the wire) divided by the true vsize of `transaction()` is strictly less than `fee_per_vbyte`. Setting `fee_per_vbyte = 1` produces a transaction whose actual fee rate is below 1 sat/vB yet which passes the `TooLowFee` check.

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

**File:** networks/bitcoin/src/wallet/send.rs (L211-243)
```rust
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
    }

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
