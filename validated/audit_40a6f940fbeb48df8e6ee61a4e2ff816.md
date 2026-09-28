### Title
OP_RETURN Data Output Excluded From Weight/Fee Calculation, Producing Under-Funded or Non-Standard Transactions - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` appends an OP_RETURN output carrying attacker-influenced `data` to the transaction's output list, but computes the transaction weight, vbytes, required fee, and the `MAX_STANDARD_TX_WEIGHT` check via `calculate_weight_vbytes`, which is called only with `payments` and the optional change output — never the OP_RETURN output. Analogous to the reference bug (a fixed scale factor applied without accounting for the actual units/size of the input), a fixed set of outputs is priced while a different, larger set is actually signed and broadcast.

### Finding Description
When `data` is specified, an OP_RETURN `TxOut` is pushed onto `tx_outs` before any size accounting is performed [1](#0-0) . However, the weight/vbyte calculation is invoked as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — `payments` does not include the OP_RETURN output [2](#0-1) . The same omission occurs in the change branch, which again passes only `payments` and the change script [3](#0-2) .

`calculate_weight_vbytes` reconstructs a full `Transaction` solely from `payments` plus an optional change output to measure weight [4](#0-3) . Consequently `needed_fee = fee_per_vbyte * vbytes` is priced for a smaller transaction than the one actually produced [5](#0-4) , and the final `MAX_STANDARD_TX_WEIGHT` check validates an understated `weight` [6](#0-5) . Up to 80 bytes of `data` are allowed [7](#0-6) , meaning the OP_RETURN output (script plus fixed 8-byte value and CompactSize length fields) can add roughly 91+ bytes (~91+ vbytes) that are neither paid for nor counted toward the standardness weight limit.

### Impact Explanation
The resulting transaction pays `fee_per_vbyte * vbytes` for a transaction that is actually up to ~91 vbytes larger, so its effective feerate is lower than intended. If the requested `fee_per_vbyte` is near the minimum relay fee, the under-estimation can push the real feerate below `DEFAULT_MIN_RELAY_TX_FEE`, producing a validly signed transaction that nodes will not relay — funds appear sent but are not spendable/confirmable until a replacement is constructed. Additionally, a transaction can pass the `MAX_STANDARD_TX_WEIGHT` check yet actually exceed it, again yielding a broadcastable-in-name-only transaction, or change handling may misprice `fee_with_change`.

### Likelihood Explanation
Any user of the processor that submits a payment with attached `data` (e.g., via an InInstruction carrying an instruction payload for the Bitcoin network) triggers the discrepancy; it is deterministic whenever `data` is non-empty. Exploitation is not adversarial so much as structural — the signed transaction itself is valid — but the invariant broken (priced size == actual size) mirrors the reference finding's priced amount != actual amount.

### Recommendation
Include the OP_RETURN output in the output set passed to `calculate_weight_vbytes` — e.g., build the candidate output list (payments + OP_RETURN + optional change) first and measure that — so `needed_fee`, the change-value check, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the final transaction.

### Proof of Concept
In `SignableTransaction::new`, `tx_outs` receives a `TxOut` for `data` at lines 194–202, yet the weight helpers at lines 204 and 225–226 are invoked with `payments` only. Construct a `SignableTransaction` with `data` of 80 bytes: `needed_fee()` and `fee()` will correspond to a transaction ~91 vbytes smaller than the `Transaction` returned by `transaction()`/`complete()`, and `weight` used in the standardness check excludes the OP_RETURN output entirely.

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

**File:** networks/bitcoin/src/wallet/send.rs (L171-173)
```rust
    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
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

**File:** networks/bitcoin/src/wallet/send.rs (L204-204)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-206)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L225-227)
```rust
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
