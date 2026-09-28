Let me check the scanner and the weight calculation more closely.### Title
`SignableTransaction::new` omits the OP_RETURN data output when calculating transaction weight, underestimating `needed_fee` - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Like the referenced report's flawed `getStablePoolTokenPrice()` valuation formula, `SignableTransaction` calculates the transaction weight/vbytes — and therefore the required fee — using a formula that omits one of the outputs actually placed in the transaction. When a `data` payload is supplied, the OP_RETURN output it creates is never counted in `calculate_weight_vbytes`, so `needed_fee` is systematically lower than the fee the finalized transaction actually requires for the requested `fee_per_vbyte`.

### Finding Description
`SignableTransaction::new` builds `tx_outs`, pushing an OP_RETURN `TxOut` when `data` is `Some`: [1](#0-0) 

However, both weight estimations pass only `payments` — not `tx_outs` — into `calculate_weight_vbytes`: [2](#0-1) [3](#0-2) 

`calculate_weight_vbytes` reconstructs a mock `Transaction` whose `output` vector contains only the `payments` (and optionally `change`); there is no parameter or path for the data output: [4](#0-3) 

`data` may be up to 80 bytes (`TooMuchData` check at lines 171-173), so the real transaction can be up to ~90+ bytes (~90 weight units... ~8-byte value + varint + script) larger than estimated. Both derived quantities are wrong:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) undercharges. Since the actual fee paid is `sum(inputs) - sum(outputs)` and the change output is sized as `input_sat - payment_sat - fee_with_change`, the real fee equals the underestimated `needed_fee` — the transaction genuinely pays less than the intended sat/vbyte rate.
2. The minimum-relay-fee check (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`, line 211) uses the same underestimated `vbytes`, so a transaction that actually falls below the node's minimum relay feerate can pass validation.

### Impact Explanation
A transaction built with `data` (a caller-supplied field of `SignableTransaction::new`, reachable purely through public API inputs) will broadcast with a feerate lower than requested and potentially below the mempool minimum relay feerate. Such a transaction is rejected by `send_raw_transaction` or sits unconfirmable, freezing the threshold wallet's selected UTXOs until a replacement is constructed — the "incorrect formula produces wrong valuation/fee" class of bug, with direct liveness/availability impact on funds.

### Likelihood Explanation
Triggered deterministically whenever `SignableTransaction::new` is called with a non-`None` `data` argument; no adversarial timing or collusion needed. The underestimation scales with the data length (worst case ~80 bytes → ~22+ missing vbytes, plus the fixed ~11-byte output overhead). Whether it crosses the relay-fee boundary depends on `fee_per_vbyte`, but the fee is wrong in all cases.

### Recommendation
Include the data output in the weight calculation — e.g., pass `tx_outs` (after the OP_RETURN push) to `calculate_weight_vbytes`, or add a `data_len`/`Option<&PushBytes>` parameter so the mock transaction contains the same OP_RETURN output. The same fix must apply to the `fee_with_change` estimation path so the change-amount subtraction accounts for the true fee.

### Proof of Concept
Call `SignableTransaction::new(inputs, payments, change, Some(vec![0xAA; 80]), fee_per_vbyte)`. The returned `SignableTransaction`'s `needed_fee()` equals `fee_per_vbyte * vbytes` computed over a transaction lacking the ~93-byte OP_RETURN output, while `transaction()` includes it. `tx.weight()` / `tx.vsize()` of the signed result exceeds the estimate, so the effective feerate `fee() / tx.vsize()` is strictly below `fee_per_vbyte`. With `fee_per_vbyte` at the relay minimum (~1 sat/vbyte ≈ passing the `TooLowFee` check on the underestimated size), the resulting ~1.09x larger transaction lands below the node's `DEFAULT_MIN_RELAY_TX_FEE` and is rejected.

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

**File:** networks/bitcoin/src/wallet/send.rs (L225-227)
```rust
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
```
