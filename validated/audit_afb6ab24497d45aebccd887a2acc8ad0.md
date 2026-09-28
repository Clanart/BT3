### Title
`SignableTransaction` fee/weight calculation omits the OP_RETURN data output, so `needed_fee` and the change amount are computed on an under-sized transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes a missing calculation whose result feeds multiple downstream computations (pool totals and exchange rate), corrupting every value derived from it. The same bug shape exists in `SignableTransaction::new`: the OP_RETURN `data` output is appended to `tx_outs` but is never included in `calculate_weight_vbytes`, so `vbytes`/`needed_fee` — which drive both the `NotEnoughFunds`/`TooLowFee` checks and the change-amount computation — are computed over a transaction that is missing an output.

### Finding Description
`SignableTransaction::new` pushes an OP_RETURN output onto `tx_outs` when `data` is provided (send.rs:194-202). However, both weight calculations only serialize `payments` (and optionally `change`) into the template transaction:

- `calculate_weight_vbytes` builds `tx.output` exclusively from `payments` plus the change script (send.rs:85-99); the `data` output is never passed in.
- The no-change call at send.rs:204 and the with-change call at send.rs:225-226 both use `tx_ins.len(), payments, ...` — never the data output.

An OP_RETURN output adds `8 (value) + 1..10 (script len) + 2 + data.len()` bytes (~90 bytes for the maximum 80-byte payload, i.e. up to ~22+ vbytes). `needed_fee = fee_per_vbyte * vbytes` therefore undercharges by `fee_per_vbyte * data_output_vbytes` every time `data` is set, and `value = input_sat - (payment_sat + fee_with_change)` sends the difference to change — meaning the final transaction's *effective* fee rate is strictly below the requested `fee_per_vbyte`, and can silently fall below `DEFAULT_MIN_RELAY_TX_FEE` even though the `TooLowFee` check at send.rs:211 passed against the undersized `vbytes`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
Every downstream value derived from `vbytes` is wrong when `data` is present:

- `needed_fee()` reports a fee insufficient for the requested rate; `fee()` (send.rs:138-141) later confirms the actual paid fee equals this same under-estimate, not the target rate.
- The `NotEnoughFunds` check accepts transactions whose real required fee is higher.
- The effective sat/vbyte of the broadcast transaction is below the requested rate and can drop below the minimum relay fee, producing a signed transaction the Bitcoin network will not relay/confirm — the spent UTXOs are effectively frozen until the error is noticed and a different transaction is produced. This is a reachable "signed artifact that does not do what was requested" defect, not a documented limitation (the docs promise "the fee necessary for this transaction to achieve the fee rate specified").

### Likelihood Explanation
Any caller constructing a `SignableTransaction` with `data: Some(...)` hits the bug unconditionally — the data output is always omitted from the weight template. Whether the resulting transaction is non-standard depends on the margin between `fee_per_vbyte` and the relay minimum; when `fee_per_vbyte` is near the floor (a common configuration), a ~22-90 vbyte omission is enough to push the effective rate below the relay minimum.

### Recommendation
Include the OP_RETURN output in the weight template: change `calculate_weight_vbytes` to take the full output list (or an extra `data: Option<&[u8]>` parameter appending the `TxOut { value: ZERO, script_pubkey: op_return }`), so `vbytes`, `needed_fee`, the `TooLowFee`/`NotEnoughFunds` checks, and the change computation all reflect the transaction that will actually be signed and broadcast.

### Proof of Concept
```rust
// Construct a transaction whose only output is an 80-byte OP_RETURN.
let inputs = vec![/* ReceivedOutput with value V */];
let data = Some(vec![0u8; 80]);
let st = SignableTransaction::new(inputs, &[], None, data, fee_per_vbyte)?;

// st.tx.output contains the OP_RETURN (~90 serialized bytes),
// but needed_fee was computed as fee_per_vbyte * vbytes(tx with NO outputs).
let actual_rate = st.fee() * 1000 / (st.transaction().vsize() as u64 * 1000);
assert!(st.needed_fee() < fee_per_vbyte * real_vbytes);
// effective sat/vbyte of st.transaction() < fee_per_vbyte
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L193-212)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
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
    }
```
