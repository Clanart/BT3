### Title
`SignableTransaction` computes `needed_fee` and the standardness weight check on a transaction that omits the OP_RETURN data output, underpaying the declared fee rate and potentially producing a non-standard transaction - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Like the Allo `fundPool` bug — where the per-transfer `msg.value < amount` check is passed while the total owed amount is never paid — `SignableTransaction::new` validates the fee and weight against a *smaller* transaction than the one actually built and signed. The fee (`needed_fee`) and the `MAX_STANDARD_TX_WEIGHT` check are computed via `calculate_weight_vbytes`, which only includes `payments` (and optionally `change`), but never the OP_RETURN output carrying `data`. The real transaction, which is what gets hashed and signed, includes the extra OP_RETURN output, so it is larger and pays a lower effective fee rate than the caller requested.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` first [1](#0-0) , but the fee is then computed from `vbytes` returned by `calculate_weight_vbytes(tx_ins.len(), payments, None)` — a function that builds its synthetic transaction only from `payments` and the optional `change` output [2](#0-1) . `data` is never passed to it, so `needed_fee = fee_per_vbyte * vbytes` under-charges by roughly `(len(data) + ~10) * fee_per_vbyte` weight-units worth of fee [3](#0-2) .

The same omission affects the change path — `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` also ignores the OP_RETURN — so `fee_with_change` is equally under-estimated [4](#0-3) . Finally, the standardness check `weight > MAX_STANDARD_TX_WEIGHT` uses the weight of the transaction *without* the OP_RETURN output, so a transaction that is actually over the standardness limit passes validation and gets signed [5](#0-4) .

Because the minimum-relay-fee check compares `needed_fee` against the same underestimated `vbytes` [6](#0-5) , none of the internal checks catch the discrepancy — exactly the same shape as the Allo report, where every individual check passes while the aggregate obligation (here, the declared `fee_per_vbyte`) is not met.

### Impact Explanation
The threshold-signed transaction pays a lower feerate than the `fee_per_vbyte` the coordinator specified. When inputs are sized tightly (`input_sat ≈ payment_sat + needed_fee`, i.e., no change output), the shortfall is real: the effective feerate of the broadcast transaction is below the declared rate, and can fall below the mempool minimum relay feerate of peers, causing the transaction to be stuck or rejected. In the worst case, if the true weight (including the OP_RETURN) exceeds `MAX_STANDARD_TX_WEIGHT`, the network produces a fully valid signature for a transaction that no standard node will relay — funds committed to those inputs are unspendable by that signed transaction and a new signing session is required.

### Likelihood Explanation
`data` is caller-supplied up to 80 bytes [7](#0-6) , and payments (which determine whether change exists and how tight the input funding is) are driven by external withdrawal requests. Any `SignableTransaction` constructed with `data` set and tightly-funded inputs deterministically underpays its declared feerate; the fee deficit scales with `fee_per_vbyte`. No malicious validator or collusion is needed — this triggers from ordinary public inputs to `SignableTransaction::new`.

### Recommendation
Include the OP_RETURN output in the synthetic transaction used by `calculate_weight_vbytes` (e.g., pass the fully-assembled `tx_outs`, or the `data` length, into the function) so that `vbytes`, `needed_fee`, `fee_with_change`, and the `MAX_STANDARD_TX_WEIGHT` check are all computed over the transaction that will actually be signed. Alternatively, move the OP_RETURN push after the weight/fee calculation but compute `calculate_weight_vbytes` over the final output set.

### Proof of Concept
Construct `SignableTransaction::new` with inputs summing to exactly `payment_sat + needed_fee` (so no change output is created) and `data = vec![0u8; 80]`:

```rust
let stx = SignableTransaction::new(
  inputs,                        // sum == payments + needed_fee
  &payments,
  None,                          // no change -> actual fee == needed_fee
  Some(vec![0u8; 80]),           // OP_RETURN output (~90 bytes) not counted in vbytes
  fee_per_vbyte,
).unwrap();

// stx.fee() == needed_fee, but stx.transaction().vsize() > vbytes used,
// so stx.fee() / actual_vsize < fee_per_vbyte — the declared rate is not paid.
```

`fee()` (line 138) returns `sum(inputs) - sum(outputs) == needed_fee`, while `self.tx` contains the extra OP_RETURN output, so the broadcast transaction's real feerate is strictly below `fee_per_vbyte`. With a large enough input count, the true weight can also exceed `MAX_STANDARD_TX_WEIGHT` even though the check at line 241 passed on the smaller weight.

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
