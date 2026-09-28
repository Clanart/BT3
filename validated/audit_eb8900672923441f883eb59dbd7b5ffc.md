### Title
`SignableTransaction` fee calculation omits the OP_RETURN output from transaction weight, undercharging the fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` lets callers attach up to 80 bytes of arbitrary `data` as an OP_RETURN output, but `calculate_weight_vbytes` is invoked with `payments` — a list that never includes that output. The `needed_fee` figure therefore pays for a transaction smaller than the one actually signed, so the real fee rate is always lower than `fee_per_vbyte` whenever `data` is provided.

### Finding Description
In `SignableTransaction::new` the OP_RETURN output is pushed onto `tx_outs` before the weight is computed: [1](#0-0) 

Yet the weight/vbyte calculation is passed `payments`, not the actual output list: [2](#0-1) 

The same omission occurs in the change-output branch, which recomputes the fee with `payments` again: [3](#0-2) 

`calculate_weight_vbytes` builds a mock `Transaction` whose outputs are exactly the `payments` it is given plus an optional change output — an OP_RETURN `TxOut` is never represented: [4](#0-3) 

An OP_RETURN output carrying the maximum allowed 80 bytes (`data.len() > 80` is rejected at line 171) adds ~90 weight units (~23 vbytes: 8-byte value + script length + `OP_RETURN` + push opcode + 80-byte payload). `needed_fee = fee_per_vbyte * vbytes` therefore underpays by `fee_per_vbyte * ~23` sats, and the signed transaction's true rate is `(inputs - outputs) / actual_vsize < fee_per_vbyte`. `fee()` confirms the actual fee is derived from real inputs/outputs, not the estimated weight: [5](#0-4) 

This is analogous to the reported bug class: the fee is charged against one notion of the transaction (payments only) while value moves on a larger actual transaction — two formulations of "the fee for this TX" that are not identical, and one is always cheaper. The `TooLowFee` guard at lines 211-213 is also applied to the undercounted vbytes, so a `fee_per_vbyte` at the relay minimum passes the check while the real transaction falls below `DEFAULT_MIN_RELAY_TX_FEE` per vbyte.

### Impact Explanation
Any caller supplying `data` produces a transaction paying a lower fee rate than specified. At minimum relay fee rates, the resulting transaction is non-standard/below minimum relay fee and will not propagate, leaving the multisig's inputs consumed by an unbroadcastable signed transaction — funds committed that cannot be spent as intended until replaced. Even at higher rates, users/protocol systematically underpay fees relative to the declared rate, degrading confirmation reliability. Severity is bounded by the ~23-vbyte per-output underestimate and single OP_RETURN output.

### Likelihood Explanation
The bug triggers deterministically on every call to `SignableTransaction::new` with `data: Some(_)`, which is reachable with public inputs (the `data` bytes are untrusted caller-controlled input to an in-scope API). The undercharge is proportional to the data length and always present; it materializes into a stuck transaction whenever `fee_per_vbyte` is at or near the minimum relay rate.

### Recommendation
Compute the weight from the actual output list (`tx_outs`) rather than `payments` — e.g., refactor `calculate_weight_vbytes` to take `&[TxOut]` and call it after the OP_RETURN output is appended — in both the no-change and with-change fee computations (send.rs:204 and send.rs:226), so `needed_fee` covers every output that will appear in the signed transaction.

### Proof of Concept
```rust
// networks/bitcoin conceptual PoC
let data = vec![0xAA; 80];
let tx = SignableTransaction::new(
  vec![input],                       // one ReceivedOutput
  &[(payment_script, 10_000)],       // one payment
  None,                              // no change
  Some(data),                        // OP_RETURN output added to tx_outs
  fee_per_vbyte,                     // e.g. 1 sat/vB
).unwrap();

// tx.output.len() == 2 (payment + OP_RETURN), but needed_fee was computed
// for a 1-output transaction.
let actual_vsize = tx.transaction().vsize() as u64;
let actual_fee = tx.fee();
// actual_fee / actual_vsize < fee_per_vbyte; shortfall ~= 23 * fee_per_vbyte.
// With fee_per_vbyte == 1, the check at send.rs:211 passes while the real
// rate is below DEFAULT_MIN_RELAY_TX_FEE (1000 sat/kvB).
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

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L193-202)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-206)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-232)
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
```
