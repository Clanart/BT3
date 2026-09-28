### Title
Attacker-controlled OP_RETURN `data` is excluded from transaction weight/fee accounting, producing transactions paying below the intended fee rate - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` appends an `OP_RETURN` output carrying caller-supplied `data` to `tx_outs`, but both weight calculations only measure the `payments` outputs plus optional `change`. The arbitrary bytes injected into the transaction are never accounted for, so `needed_fee` is computed against a smaller transaction than the one actually produced and signed. The analog to the Cobbler template injection is that attacker-supplied content is consumed by a privileged operation (threshold signing of a bridge transaction) without being fully represented in the operation's cost/validity accounting.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`):

- The `OP_RETURN` output is pushed onto `tx_outs` at lines 194-202, before any weight is measured.
- The fee is then estimated via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204 and again, with change, at line 226. `calculate_weight_vbytes` (lines 62-127) builds the measurement `Transaction` solely from `payments` and `change`; `tx_outs` — which already contains the `data` output — is never consulted.
- `needed_fee = fee_per_vbyte * vbytes` (line 206) and the minimum-relay-fee check (line 211) therefore operate on a transaction that is missing up to ~91 serialized bytes (an 80-byte push plus output overhead), i.e., ~91 vbytes.

The signed transaction's actual fee is `input_sat - payment_sat - change = needed_fee` (lines 228-233), so the transaction ends up paying `needed_fee` for a larger `vsize` than assumed. The effective fee rate is strictly lower than `fee_per_vbyte`, and when `fee_per_vbyte` was chosen near the minimum relay fee, the resulting transaction falls under `DEFAULT_MIN_RELAY_TX_FEE` for its true size and will not propagate.

### Impact Explanation
The `data` field is populated from `InInstruction` data supplied by an external Bitcoin sender — an unprivileged party controls these bytes. By attaching ~80 bytes of data to an incoming transaction, the attacker causes the coordinator to construct and threshold-sign a `SignableTransaction` whose `needed_fee` was computed without that output. The signed transaction is underpriced relative to the requested rate and can fall below the mempool's minimum relay fee, leaving the batch/withdrawal transaction unable to propagate and the associated funds unspendable until a new signing session at a corrected fee is scheduled. This is attacker-triggered mis-pricing of every transaction carrying InInstruction data, not merely a rounding edge case.

### Likelihood Explanation
Reachability is direct: any user sending BTC to Serai with an embedded InInstruction supplies the `data` passed to `SignableTransaction::new`. Maximum-size data maximizes the underestimate (~90+ vbytes per transaction). Whether it converts to a relay failure depends on how close the requested `fee_per_vbyte` is to the relay minimum, but the fee rate is always lower than intended.

### Recommendation
Include the `OP_RETURN` output in the weight accounting: either push the data output before both `calculate_weight_vbytes` calls and pass the full output list, or pass `tx_outs` (payments + data + optional change) to the estimator. Additionally, recompute the minimum-fee check against the transaction's actual serialized size, e.g., verify `needed_fee >= DEFAULT_MIN_RELAY_TX_FEE * actual_vsize / 1000` using the constructed `tx` rather than the synthetic estimate.

### Proof of Concept
```rust
// Conceptual: in SignableTransaction::new (networks/bitcoin/src/wallet/send.rs),
// construct inputs/payments such that input_sat covers payments + fee exactly,
// with data = Some(vec![0; 80]).

let inputs = vec![received_output];                    // e.g. 10_000 sat input
let payments = [(payment_script, 9_000)];              // payment
let data = Some(vec![0u8; 80]);                        // attacker-controlled OP_RETURN
let tx = SignableTransaction::new(inputs, &payments, None, data, 1).unwrap();

// The estimator measured only the payment output (~43 vbytes extra for the
// OP_RETURN output was omitted). The real transaction contains the OP_RETURN.
let actual_vbytes = tx.transaction().vsize() as u64;
// tx.fee() == needed_fee, computed for a ~91-byte-smaller transaction:
assert!(tx.fee() < actual_vbytes * 1 /* fee_per_vbyte */);
// If needed_fee < DEFAULT_MIN_RELAY_TX_FEE * actual_vbytes / 1000, the
// signed transaction is rejected by Bitcoin relay policy despite passing
// the TooLowFee check on line 211.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
