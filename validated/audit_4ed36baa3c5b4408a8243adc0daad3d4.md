### Title
Fee/weight estimation omits the OP_RETURN output appended to the transaction — ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
The bug class in the external report is a resource bound (gas limit) being computed before extra bytes are attached to the payload, so the bound doesn't reflect the transaction actually submitted. The same shape exists in `SignableTransaction::new`: the OP_RETURN output carrying the caller's `data` is appended to `tx_outs` before the weight/vbyte computation, but `calculate_weight_vbytes` is invoked with `payments` — a list that excludes that OP_RETURN output — so the estimated weight and the resulting fee ignore the extra output entirely.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` at `send.rs:194-202`. Immediately after, weight and vbytes are computed via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at `send.rs:204`, which builds its dummy transaction's `output` solely from `payments` (`send.rs:85-93`). `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`) and the minimum-relay-fee check (`send.rs:211`) therefore both use a vbyte count that omits the OP_RETURN output.

The same omission occurs on the change path: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at `send.rs:225-226` again passes only `payments`. The change output's value is then computed as `input_sat - (payment_sat + fee_with_change)` (`send.rs:228-230`), so the transaction's actual fee (`sum(inputs) - sum(outputs)`, per `fee()` at `send.rs:138-141`) equals the underestimated `needed_fee`. Unlike the original bug, where the code at least added a per-byte gas constant for the appended byte, nothing here compensates for the appended output's weight.

The OP_RETURN output's serialized size is roughly `8 (value) + 1 (script len) + 1 (OP_RETURN) + push opcode + data.len()` bytes, all non-witness, so it contributes `4×` that to weight and its full size to vbytes. `SignableTransaction::new` accepts attacker-influenced `data` (the `data` argument flows from batch/in-instruction payloads), scaling the discrepancy with payload length.

### Impact Explanation
The produced transaction pays a fee lower than both the requested `fee_per_vbyte` rate and potentially the `DEFAULT_MIN_RELAY_TX_FEE` floor it was checked against. The min-relay check passes on the underestimated size while the real transaction is larger, so a crafted `data` length can push the actual fee rate below relay minimum, causing nodes to reject the broadcast. For a threshold-signed Bitcoin transaction this yields a fully signed transaction that cannot be relayed/confirmed — funds remain locked in the multisig output and the signed attempt must be abandoned and re-signed, a liveness failure on the spend path reachable purely through the `data` a caller supplies.

### Likelihood Explanation
The bug is deterministic: any call to `SignableTransaction::new` with `data: Some(_)` produces an underestimated fee; no adversarial timing or validator collusion is needed. Whether it crosses into rejection depends on `fee_per_vbyte`, data length, and the change-branch margin, but it triggers on a normal, publicly reachable API call. Severity is medium: no secret leakage or forgery, but a concrete incorrect bound on a signed transaction that can render the spend non-broadcastable.

### Recommendation
Compute weight over the actual `tx_outs` (or the full final `Transaction`) rather than `payments`, e.g., build `tx_outs` first — including the OP_RETURN output — and have `calculate_weight_vbytes` take the assembled outputs (plus the candidate change output) so estimation happens on the same artifact that will be signed and broadcast, mirroring the report's fix of estimating just before `craftTx`.

### Proof of Concept
1. Call `SignableTransaction::new(inputs, payments, change, Some(vec![0u8; N]), fee_per_vbyte)` where `inputs` exactly cover `payment_sat + needed_fee + DUST` so a change output is created.
2. Compute the transaction's real vsize from `SignableTransaction::transaction()` including the OP_RETURN output; compute `actual_fee_rate = fee() / real_vbytes`.
3. `actual_fee_rate < fee_per_vbyte` by `op_return_vbytes / real_vbytes` of the requested rate. With `N` chosen so the real vsize is large relative to `needed_fee`, `actual_fee_rate * 1000 < DEFAULT_MIN_RELAY_TX_FEE`, so the signed transaction is non-standard for relay despite passing the check at `send.rs:211`.

Relevant code: [1](#0-0) , [2](#0-1) , [3](#0-2)

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

**File:** networks/bitcoin/src/wallet/send.rs (L193-213)
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
