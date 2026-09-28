### Title
`SignableTransaction::new` under-prices the fee by excluding the OP_RETURN `data` output from the weight/vbytes calculation, producing transactions whose effective fee rate is below the intended rate and potentially below the minimum relay fee - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the buy/sell rounding asymmetry (two code paths compute a price for the same quantity with different rounding, letting one side underpay), `SignableTransaction::new` computes `needed_fee` and the minimum-relay-fee check against a transaction weight that omits the `data` (OP_RETURN) output, while the actual signed transaction includes it. The "estimated" transaction is smaller than the "real" transaction, so the fee rate actually paid per vbyte is lower than `fee_per_vbyte`, and can fall below `DEFAULT_MIN_RELAY_TX_FEE`, yielding a transaction the network will not relay/confirm.

### Finding Description
`calculate_weight_vbytes` reconstructs a template `Transaction` whose `output` list is built solely from `payments` plus an optional `change` output [1](#0-0) . In `SignableTransaction::new`, the OP_RETURN output is pushed into `tx_outs` *before* the weight is calculated, but the weight function is called with `payments`, not `tx_outs`, so the data output (up to 80 bytes of pushed data plus script/amount overhead, ~90+ vbytes) is never included [2](#0-1) .

`needed_fee = fee_per_vbyte * vbytes` is then computed on the underestimated vbytes [3](#0-2) , and the minimum-relay check `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` also uses the same underestimated `vbytes` [4](#0-3) . When the leftover is insufficient for a dust change output (or no change address is given), the actual fee paid equals roughly `input_sat - payment_sat`, which may be exactly `needed_fee`. That fee is then spread across a real transaction up to ~90 vbytes larger than estimated, so the true sat/vbyte rate is strictly lower than `fee_per_vbyte` and can be under 1 sat/vbyte — rejected by relay policy.

This mirrors the report's class: the "buy" side (fee estimation path) rounds/excludes data that the "sell" side (the actual funded transaction) must cover, so the contract-equivalent invariant "fee paid ≥ rate × size" is violated exactly when the margin is tight.

### Impact Explanation
An unprivileged party controls the `data` bytes passed to `SignableTransaction::new`. A crafted transaction that would pass the minimum-fee check instead produces a signed transaction below the minimum relay fee rate. The inputs are then consumed by a transaction that cannot propagate or confirm; with `sequence = Sequence::MAX` there is no signaling for replacement, and without a change output there is no way to fee-bump. The spent inputs are effectively frozen, matching the "funds committed to a transaction that cannot be executed" impact of the original finding.

### Likelihood Explanation
Requires `data` to be supplied together with a tight input/payment margin (leftover below `DUST` so no change output is added and the fee isn't incidentally padded). Within the wallet API this is a normal parameter combination; any caller attaching an OP_RETURN payload while spending nearly all inputs hits it deterministically whenever `fee_per_vbyte` is at/near the minimum. Medium likelihood, medium impact.

### Recommendation
Include the OP_RETURN output in the template transaction inside `calculate_weight_vbytes` (pass `tx_outs`, or the data length, into the estimator), analogous to rounding the "buy" price up. Alternatively, compute `needed_fee = fee_per_vbyte * vbytes_of_actual_tx` after all outputs are assembled, and run the `DEFAULT_MIN_RELAY_TX_FEE` check against the final transaction's vsize.

### Proof of Concept
```rust
// In networks/bitcoin/src/wallet/send.rs construction:
// inputs = [one output of value = DUST_payment + needed_fee_estimated]
// payments = [(addr, payment_amount)], change = None,
// data = Some(vec![0; 80])  // max allowed by the TooMuchData check

let tx = SignableTransaction::new(
    inputs, &payments, /* change */ None, /* data */ Some(vec![0; 80]), fee_per_vbyte,
).unwrap();

// vbytes was computed WITHOUT the ~90-vbyte OP_RETURN output.
// needed_fee == fee_per_vbyte * vbytes_small
// actual fee == input_sat - payment_sat == needed_fee (no change created)
// actual vsize == vbytes_small + ~90
// effective rate = needed_fee / actual_vsize < fee_per_vbyte
// With fee_per_vbyte = 1, effective rate < 1 sat/vbyte -> below
// DEFAULT_MIN_RELAY_TX_FEE despite passing the TooLowFee check,
// because that check also used the underestimated vbytes.
// The signed transaction cannot be relayed; its inputs are stuck.
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

**File:** networks/bitcoin/src/wallet/send.rs (L194-204)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-206)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L211-213)
```rust
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }
```
