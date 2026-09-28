### Title
OP_RETURN data output omitted from weight/fee calculation causes underpaid transaction fees - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` adds an OP_RETURN `data` output to the transaction but computes the transaction weight/vbytes — and therefore `needed_fee` — using only the payment outputs. This is the same bug class as the reference report: a parameter to the fee calculation is hardcoded (here, the output set used for size estimation silently excludes a real output), causing the produced transaction to systematically underpay its intended fee rate. In edge cases the transaction can even fall below the Bitcoin minimum relay fee and fail to propagate, mirroring the report's "underpay → destination execution fails (OOG)" outcome.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` when `data` is `Some` [1](#0-0) . However, the subsequent fee estimation calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` which builds a dummy transaction whose `output` vec contains only `payments` — the `data` output is never included [2](#0-1) . The same omission occurs for the with-change estimate [3](#0-2) .

Because `needed_fee = fee_per_vbyte * vbytes` is derived from this underestimated `vbytes` [4](#0-3) , the final transaction (which does include the up-to-80-byte `data` output) is larger than estimated and therefore pays a lower effective fee rate than requested. Additionally, the `TooLowFee` guard uses the same underestimated `vbytes` [5](#0-4) , so a transaction whose true size puts it below `DEFAULT_MIN_RELAY_TX_FEE` is accepted as valid.

### Impact Explanation
Any `SignableTransaction` constructed with a `data` payload pays `fee_per_vbyte * (actual_vbytes - estimated_vbytes)` less than intended — roughly `fee_per_vbyte * (~9 + data_len)` satoshis short, up to ~90 vbytes unaccounted for a maximal 80-byte payload. When `change` is present, the shortfall comes out of the intended fee directly. When the shortfall pushes the real feerate under the node's relay minimum, the signed transaction will not be relayed or mined — a hard availability failure for funds the transaction moves, analogous to the underpaid LayerZero fee causing destination execution failure in the reference report.

### Likelihood Explanation
Deterministic: it occurs on every call with `data.is_some()`. The magnitude is bounded (~90 vbytes), so the practical impact is concentrated on transactions using a low `fee_per_vbyte` and/or large `data` payloads, but the miscalculation always happens.

### Recommendation
Include the `data` output in the transaction built inside `calculate_weight_vbytes` (e.g., pass the data/`tx_outs` actually being committed to, not just `payments` and `change`), so `weight`, `vbytes`, `needed_fee`, and the `TooLowFee` check reflect the transaction that will actually be signed and broadcast. Alternatively, construct the final `tx` first and compute weight/vbytes from it directly.

### Proof of Concept
```rust
// networks/bitcoin context; pseudo-PoC
let inputs = vec![received_output];           // some scanned ReceivedOutput
let payments = vec![(pay_script, 1_000)];
let data = vec![0u8; 80];                      // max-size OP_RETURN payload

let tx = SignableTransaction::new(inputs, &payments, None, Some(data), fee_per_vbyte).unwrap();
// tx.needed_fee() == fee_per_vbyte * vbytes(without OP_RETURN output)
// Actual serialized tx contains the extra OP_RETURN output (~91 extra vbytes),
// so effective feerate = tx.fee() / actual_vsize < fee_per_vbyte.
// With fee_per_vbyte = 1 (min relay), actual feerate < 1 sat/vbyte -> rejected by relay.
```
The size accounting gap is directly visible by comparing `calculate_weight_vbytes`'s dummy `tx.output` (payments + optional change only) with the real `tx_outs` (payments + OP_RETURN + optional change).

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L85-94)
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

**File:** networks/bitcoin/src/wallet/send.rs (L225-227)
```rust
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
```
