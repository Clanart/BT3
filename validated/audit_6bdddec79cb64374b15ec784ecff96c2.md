The bug class: a quantity is accounted for in one calculation but omitted from another, causing an incorrect computed value. In Serai's `SignableTransaction::new`, the OP_RETURN `data` output is appended to `tx_outs` but never included in the weight/vbytes calculation used to compute the fee — while the actual transaction does carry that output. Confirming the exact lines:### Title
`SignableTransaction::new` omits the OP_RETURN data output when calculating transaction weight and fee - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` appends an `OP_RETURN` output carrying caller-supplied `data` to `tx_outs`, but computes `needed_fee`, `weight`, and the `MAX_STANDARD_TX_WEIGHT` check via `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which only accounts for `payments`. This is the same bug class as the GMX report: a component of the real transaction (`data` output, analog of `fees.feeAmountForPool`) is included in the actual object but excluded from the derived calculation, producing an incorrect result.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, the OP_RETURN output is pushed onto `tx_outs` at lines 194-202. However, both `calculate_weight_vbytes` calls — the no-change call at line 204 and the with-change call at line 226 — are given only `payments`, so neither the OP_RETURN output's `script_pubkey` size (up to ~83 bytes for the max 80-byte payload) nor its overhead is ever counted. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` (lines 206, 227, 232) is computed on a smaller vbytes than the final transaction's actual vsize, so the transaction pays a lower effective fee rate than `fee_per_vbyte` requested.
2. The minimum-relay-fee check at line 211 and the `MAX_STANDARD_TX_WEIGHT` check at line 241 are evaluated against the underestimated weight.
3. When `change` is specified, the change output value `input_sat - payment_sat - fee_with_change` (line 228) is inflated by exactly the missing fee, while the actual fee (`sum(inputs) - sum(outputs)`, `fee()` at line 138) remains the under-estimated `needed_fee`.

`data` is an untrusted public input to `SignableTransaction::new` — any caller (or any protocol feeding caller-controlled memo data into a send) can reach this path with up to 80 bytes.

### Impact Explanation
- The signed transaction's true fee rate is strictly below the requested `fee_per_vbyte`; with a large `data` payload the discrepancy is ~89 vbytes × `fee_per_vbyte` satoshis, and the effective feerate can fall below `DEFAULT_MIN_RELAY_TX_FEE` even though the check passed, yielding a transaction the network will not relay/confirm.
- A transaction near the standardness weight limit passes the `MAX_STANDARD_TX_WEIGHT` check while its real weight exceeds it, producing a non-standard, unbroadcastable signed transaction — funds committed to `tx_outs` are not spendable through this TX as built.
- When change exists, the under-charged fee is silently absorbed into the change output rather than being paid, masking the error from accounting that trusts `needed_fee`/`fee()`.

### Likelihood Explanation
Deterministic whenever `data.is_some()`: the fee and weight are always miscalculated by the size of the OP_RETURN output. Exploitation requires only supplying `data` to `SignableTransaction::new`; no attacker privilege beyond controlling the memo field of a send is needed. Impact severity is bounded (mispriced fee / potential non-standard TX rather than theft), consistent with Medium.

### Recommendation
Include the data output in weight estimation. Pass the fully-built `tx_outs` (or an equivalent list including the OP_RETURN script) into `calculate_weight_vbytes` for both the no-change and with-change calls:

```diff
- let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
+ let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), &all_outputs, None);
```

where `all_outputs` reflects `payments` plus the `TxOut` for `data` (its `script_pubkey` length is what matters). Apply the same fix at the `Some(&change)` call site so `fee_with_change` covers the OP_RETURN output as well.

### Proof of Concept
```rust
// networks/bitcoin: construct a SignableTransaction with data and change
let data = vec![0u8; 80]; // max allowed payload
let tx = SignableTransaction::new(
    vec![input],                       // some ReceivedOutput
    &[(payment_script, payment_amt)],
    Some(change_script),
    Some(data.clone()),
    fee_per_vbyte,
).unwrap();

// The real transaction includes the OP_RETURN output:
let actual_vsize = tx.transaction().vsize() as u64;
// needed_fee was computed as fee_per_vbyte * vbytes where vbytes
// excluded the ~89-vbyte OP_RETURN output:
assert!(tx.needed_fee() < fee_per_vbyte * actual_vsize);
// Effective feerate is below the requested rate:
let effective_rate = tx.fee() as f64 / actual_vsize as f64;
assert!(effective_rate < fee_per_vbyte as f64);
```

Relevant code: `tx_outs.push(OP_RETURN)` at send.rs:194-202; `calculate_weight_vbytes(tx_ins.len(), payments, None)` at send.rs:204 and `Some(&change)` at send.rs:225-227; `calculate_weight_vbytes` builds its dummy TX from `payments` only at send.rs:62-99. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** networks/bitcoin/src/wallet/send.rs (L193-206)
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
