### Title
Fee/weight calculation omits the OP_RETURN data output, producing an under-priced (potentially unrelayable) transaction - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary

The Tessera report describes an accounting mismatch: `purchase` allows paying less than `minReservePrices * filledQuantities`, while `claim` still debits contributors at the assumed reserve price — the assumed cost and the actual cost diverge, and the difference is lost. In `bitcoin-serai`, `SignableTransaction::new` exhibits the same class of bug in the opposite direction: the transaction builder commits to a `needed_fee` and change amount computed from an *assumed* transaction shape that omits the OP_RETURN data output, while the *actual* signed transaction includes it. The assumed weight and the actual weight diverge, so the signed transaction pays a lower fee rate than requested and can fall below the network's minimum relay fee.

### Finding Description

`SignableTransaction::new` pushes the OP_RETURN output into `tx_outs` **before** the weight/vbytes computation, but `calculate_weight_vbytes` rebuilds the transaction purely from `payments` (plus optional `change`) and never includes the data output: [1](#0-0) 

```rust
// Add the OP_RETURN output
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

while `calculate_weight_vbytes` only collects `payments`: [2](#0-1) 

The same omission affects the change path: `fee_with_change` is derived from `vbytes_with_change`, which also excludes the data output: [3](#0-2) 

Consequences of the assumption/actual divergence:

1. `needed_fee` is understated by `fee_per_vbyte * (size of OP_RETURN output)` — up to ~90 vbytes for the maximum allowed 80-byte payload (`data` is capped at 80 bytes at line 171-173). `needed_fee()` is documented as "the fee necessary for this transaction to achieve the fee rate specified at construction," which is false.
2. The `TooLowFee` check compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the *understated* `vbytes`. The actual transaction is larger, so its real fee rate can drop below `DEFAULT_MIN_RELAY_TX_FEE`, producing a validly-signed transaction that nodes will not relay or mine.
3. When a change output exists, it is over-credited: `value = input_sat - payment_sat - fee_with_change` hands the sats that should have paid for the data output's bytes to the change output instead of the fee, compounding the underpayment.
4. The `TooLargeTransaction` check (`weight > MAX_STANDARD_TX_WEIGHT`) uses the underestimated `weight`, so a transaction can exceed the standardness weight limit while passing the check.

Like the original finding — where `claim` charged users at the assumed `minReservePrices` rather than the actual purchase price — the produced transaction is signed over a value assumption (fee paid = assumed vbytes × rate) that does not hold for the actual transaction. The signing path (`TransactionSignMachine::sign`) then commits signatures to this mispriced transaction via `Prevouts::All`: [4](#0-3) 

### Impact Explanation

A `SignableTransaction` constructed with a non-empty `data` payload has a real fee rate strictly lower than `fee_per_vbyte`. When the change output is created, the missing fee is silently extracted as extra change, degrading effective fee rate further relative to intent. At low `fee_per_vbyte` values (e.g., just passing the `TooLowFee` bound at ~1 sat/vb), the actual fee rate falls under `DEFAULT_MIN_RELAY_TX_FEE`, so the fully signed transaction is non-standard and will be rejected by relay nodes — the multisig's inputs become locked in a signed-but-unbroadcastable transaction until a new signing round with corrected fees is performed. This is a concrete loss-of-liveness / incorrect-accounting bug reachable purely through public inputs (`data`, `fee_per_vbyte`) to the wallet API.

### Likelihood Explanation

Any caller passing `data` (an OP_RETURN payload is an explicitly supported, public feature of `SignableTransaction::new`) triggers the miscalculation deterministically — no edge conditions required. Whether it causes a non-relayable transaction depends on `fee_per_vbyte` and payload size, but `needed_fee()` is wrong in 100% of cases where `data.is_some()`. The underpayment is bounded (~90 vbytes worth), keeping severity at Medium rather than High.

### Recommendation

Pass the data output into `calculate_weight_vbytes` (e.g., an `Option<&ScriptBuf>`/`data_len` parameter) so both `vbytes` and `vbytes_with_change` — and therefore `needed_fee`, the `TooLowFee` bound, the change value, and the `MAX_STANDARD_TX_WEIGHT` check — reflect the actual transaction. Alternatively, construct the real `tx_outs` (payments + OP_RETURN + change) first and compute weight from that exact output set, mirroring the original report's mitigation of updating the recorded value to the actual one (`_price / filledQuantities[_poolId]` → `actual_vbytes`).

### Proof of Concept

```rust
// Conceptual: construct a transaction with a data payload
let data = Some(vec![0u8; 80]);
let tx = SignableTransaction::new(inputs, &payments, Some(change), data.clone(), fee_per_vbyte)?;

// The signed transaction contains the OP_RETURN output...
assert!(tx.transaction().output.iter().any(|o| o.script_pubkey.is_op_return()));

// ...but needed_fee was computed as if it didn't exist.
// Actual fee rate = tx.fee() / actual_vsize < fee_per_vbyte,
// because actual_vsize includes the ~90-byte OP_RETURN output
// that calculate_weight_vbytes(tx_ins.len(), payments, None) ignored.
```

Concretely: with `fee_per_vbyte = 1` and vbytes such that `needed_fee` just exceeds `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`, the real transaction's vsize is ~90 bytes larger, pushing its effective fee rate below 1 sat/vb — a signed transaction no relay node will accept, despite `SignableTransaction::new` returning `Ok`.

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

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
```rust
    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
        )?;
```
