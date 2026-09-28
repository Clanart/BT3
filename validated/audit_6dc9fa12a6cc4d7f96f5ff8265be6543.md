### Title
Fee/weight calculation ignores the OP_RETURN data output, producing under-priced transactions that cannot relay - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` builds the final transaction's output list (including an optional OP_RETURN data output), but computes the fee, the minimum-relay-fee check, and the maximum-weight check against a synthetic transaction that omits the data output entirely. The result is an "accounting vs. reality" mismatch analogous to paying only the delta while being credited the full amount: the transaction is signed and accounted as paying `needed_fee`, while the actual serialized transaction is larger than what the fee was priced for, so its effective fee rate falls below the requested rate and potentially below the network's minimum relay fee — rendering the signed transaction unbroadcastable and the plan's funds stuck.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` before the weight/fee calculation, yet `calculate_weight_vbytes` is invoked with `payments` only:

```rust
// send.rs
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
``` [1](#0-0) 

`calculate_weight_vbytes` only serializes `payments` plus optional `change` — the `data` output is never part of the measured transaction [2](#0-1) . The same omission occurs in the change path (`calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`) [3](#0-2) , and in the `MAX_STANDARD_TX_WEIGHT` check which uses the understated `weight` [4](#0-3) .

An 80-byte OP_RETURN adds roughly 90 vbytes (~364 weight units: 8-byte amount + varint + script). For a typical ~150 vbyte transaction, the actual size is ~40% larger than the priced size, so `tx.fee() / actual_vsize` can drop below `DEFAULT_MIN_RELAY_TX_FEE` even though the check passed.

### Impact Explanation
Any caller supplying `data` to `SignableTransaction::new` obtains a signed transaction whose effective fee rate is lower than `fee_per_vbyte` and can be below the minimum relay fee. Such a transaction is rejected by `send_raw_transaction`/mempool admission, so the payment never confirms and the consumed inputs remain locked in the plan until manually reconstructed — funds are committed to a transaction that is not spendable/relayable. The understated `weight` also means a borderline-size transaction plus data can exceed `MAX_STANDARD_TX_WEIGHT` without triggering `TooLargeTransaction`, another non-standard (unrelayable) outcome.

### Likelihood Explanation
Deterministic whenever `data` is supplied and `fee_per_vbyte * vbytes_without_data < min_relay * (vbytes_with_data)`, which is the common case at low fee rates (e.g., 1 sat/vb). No special privileges or timing are needed — only control over the `data` field of a payment being signed.

### Recommendation
Pass the fully constructed `tx_outs` (or an equivalent output list including the data output) into `calculate_weight_vbytes` — e.g., change its signature to accept the `Vec<TxOut>` already built — for both the no-change and with-change calculations, and use the same fully-populated transaction for the `MAX_STANDARD_TX_WEIGHT` check.

### Proof of Concept
```rust
// One input of 10_000 sats, one payment of 5_000, 80 bytes of data,
// fee_per_vbyte chosen so needed_fee barely clears the min-relay check.
let tx = SignableTransaction::new(
  vec![input],                                   // ReceivedOutput with value >= payment + fee
  &[(payment_script, 5_000)],
  None,
  Some(vec![0xAA; 80]),                          // ~90 extra vbytes not counted
  fee_per_vbyte,                                 // e.g. 1 sat/vbyte
).unwrap();
// needed_fee = fee_per_vbyte * vbytes(without data)
// tx has the OP_RETURN output => actual vsize is ~40% larger
// effective rate = tx.fee() / tx.vsize() < DEFAULT_MIN_RELAY_TX_FEE
assert!(tx.fee() * 1000 / (tx.vsize() as u64) <
        u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE));
// rpc.send_raw_transaction(&tx) is rejected: "min relay fee not met"
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

**File:** networks/bitcoin/src/wallet/send.rs (L195-212)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
