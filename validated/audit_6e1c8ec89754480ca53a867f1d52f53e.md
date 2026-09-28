### Title
Fee/weight estimation omits the OP_RETURN output, so transactions carrying data pay a lower effective fee rate than requested - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The analog of the reported bug (a loop allocating proportional shares `weight_i * total / total_weight` that loses the remainder to rounding, leaving part of the budget unallocated) is `SignableTransaction::new`'s fee calculation. `calculate_weight_vbytes` builds a throwaway `Transaction` to measure weight/vbytes, but its `output` list is built **only** from `payments`; the `data` argument's OP_RETURN output — which `new` has already pushed onto `tx_outs` — is never included in the measured transaction. Just as the Voter algorithm silently drops part of `totalPower`, the fee estimator silently drops part of the transaction's real size, so `needed_fee = fee_per_vbyte * vbytes` is computed against an underestimated vsize.

### Finding Description
- `calculate_weight_vbytes` constructs the measurement `Transaction` with `output` = `payments` (plus optional change) only; `data` is not a parameter and is never represented [1](#0-0) .
- `new` pushes an OP_RETURN output carrying up to 80 bytes (`data.as_ref().map_or(0, Vec::len) > 80` is rejected) into `tx_outs` *before* the weight/vbytes are measured [2](#0-1) .
- Both `needed_fee = fee_per_vbyte * vbytes` and the minimum-relay-fee check `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` therefore use a vbytes value missing the ~90+ serialized bytes of the OP_RETURN output [3](#0-2) .
- The change branch has the same flaw: `fee_with_change = fee_per_vbyte * vbytes_with_change` is measured on a tx still lacking the data output, and `change = input_sat - payment_sat - fee_with_change` then diverts the difference into the change output rather than the fee [4](#0-3) .

### Impact Explanation
Every transaction built with `data` pays `fee_per_vbyte * (true_vbytes − ~90)` less than intended: the effective sat/vbyte rate falls below the caller-specified `fee_per_vbyte` and can fall below the network's minimum relay fee even though the explicit min-fee check passed (it was evaluated against the underestimated `vbytes`). The signed transaction commits to this low fee, so it may fail to relay or remain unconfirmed, stalling the payments/change it carries — a DoS/stuck-funds condition analogous to the report's "voting power left unapplied." Severity is bounded because only up to ~80 bytes of data is omitted and funds are not directly stealable, warranting Medium severity.

### Likelihood Explanation
The bug triggers deterministically whenever `SignableTransaction::new` is invoked with `Some(data)` — no adversarial input is needed beyond a code path that attaches an OP_RETURN. The underestimate is proportional to the data length, and it is largest (in relative terms) exactly when `fee_per_vbyte` is low, which is also when the min-relay check matters most. If no caller ever passes `data`, the flawed branch is dead code; that is the main reachability caveat.

### Recommendation
Include the OP_RETURN output in the transaction used by `calculate_weight_vbytes` — e.g., pass `data` (or the fully-built `tx_outs`, minus change) into the estimator — and recompute `vbytes` over the true output set. Mirror the report's "allocate the last share by subtraction" fix by computing the change amount only after the *final* tx (payments + data + change) has been measured, so the fee actually paid equals `fee_per_vbyte * real_vbytes`. Re-run the min-relay check against the final vbytes.

### Proof of Concept
```rust
// In networks/bitcoin/src/wallet/tests or a unit test on SignableTransaction:
let data = vec![0u8; 80];
let tx_with_data = SignableTransaction::new(
    inputs.clone(), &payments, change.clone(), Some(data.clone()), fee_per_vbyte
).unwrap();

// The transaction Serai actually signs contains the OP_RETURN output:
assert!(tx_with_data.tx.output.iter().any(|o| o.script_pubkey.is_op_return()));

// Recompute the true vsize of the signed transaction:
let actual_vbytes = bitcoin::policy::get_virtual_tx_size(
    i64::try_from(tx_with_data.tx.weight().to_wu()).unwrap(), 0);

// The fee budget was priced on a tx WITHOUT the OP_RETURN output:
assert!(tx_with_data.needed_fee() < fee_per_vbyte * actual_vbytes as u64);
// => effective feerate = needed_fee / actual_vbytes < fee_per_vbyte,
//    and may be < DEFAULT_MIN_RELAY_TX_FEE/1000 despite the check passing.
```
Root cause confirmed: `calculate_weight_vbytes` at `networks/bitcoin/src/wallet/send.rs:62-127` never receives or encodes the `data` output that `new` appends at lines 194-202.

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

**File:** networks/bitcoin/src/wallet/send.rs (L206-212)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-234)
```rust
    // If there's a change address, check if there's change to give it
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
