### Title
`SignableTransaction::new` omits the OP_RETURN output from the weight/vbytes fee calculation, underpaying fees and risking stuck transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to a `payable` function that accepts value it never uses (locking that value), `SignableTransaction::new` accepts an optional `data` payload that materially affects the transaction's size — and therefore its required fee — yet `calculate_weight_vbytes` never accounts for the OP_RETURN output it creates. The committed fee is computed on a transaction shape smaller than the one actually signed and broadcast, so the transaction pays a lower effective fee rate than requested and can be under the minimum relay fee entirely.

### Finding Description
In `SignableTransaction::new`, when `data` is `Some`, an OP_RETURN output is pushed onto `tx_outs` before the fee computation: [1](#0-0) 

However, the subsequent call to `calculate_weight_vbytes` only builds the dummy transaction's outputs from `payments` (plus the optional change output) — the OP_RETURN output is never included: [2](#0-1) [3](#0-2) 

The same omission occurs in the change-output path: [4](#0-3) 

An OP_RETURN output carrying up to the allowed 80 bytes adds roughly 95–100 bytes (~25 vbytes) that is entirely missing from `vbytes`/`vbytes_with_change`. Consequently:

- `needed_fee` is `fee_per_vbyte * vbytes` for a transaction that is actually larger, so the achieved fee rate is strictly below the caller's `fee_per_vbyte`.
- The minimum-fee check `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` is evaluated against the understated vbytes, so a transaction that would be rejected as non-standard at its true size can pass this gate.
- Since the change output is computed as `input_sat - (payment_sat + fee_with_change)`, the actual paid fee is exactly the understated `fee_with_change` — the error is not compensated for anywhere.
- `needed_fee()` publicly reports the understated value to integrators.

### Impact Explanation
A caller supplying both `data` and `change` produces a transaction whose real fee rate is lower than requested. If the true required minimum relay fee exceeds the committed fee (which requires only ~1000 vbytes of undercount at a 1 sat/vbyte margin, achievable when `needed_fee` barely clears the relay floor on the understated size), the transaction fails to relay. Otherwise it propagates at a lower fee rate than intended and can remain unconfirmed indefinitely, effectively locking the spent inputs until replaced. The user supplied a resource (the data payload) that the fee logic never consumed — the direct analog of ETH sent to a payable function that never uses it.

### Likelihood Explanation
Any unprivileged caller of `SignableTransaction::new` who supplies `data` triggers the miscalculation deterministically; no adversary is required. The likelihood of a stuck/non-relaying transaction depends on how close `fee_per_vbyte` is to the relay minimum and how large `data` is (up to 80 bytes ≈ 25 vbytes of uncounted weight). Given `fee_per_vbyte` is typically chosen near the market minimum, this is reachable in normal operation.

### Recommendation
Pass the OP_RETURN output (or its serialized size) into `calculate_weight_vbytes`. The simplest fix is to build the dummy transaction's outputs from the already-constructed `tx_outs` (payments + OP_RETURN) rather than from `payments` alone — e.g., change the signature to accept the full `tx_outs` slice plus the optional change script — so all outputs that will appear in the final transaction are counted in both the no-change and with-change weight calculations.

### Proof of Concept
```rust
// Conceptual: compare committed fee vs. actual size
let data = vec![0u8; 80];
let st = SignableTransaction::new(
    inputs,                 // ReceivedOutputs covering payments + fee
    &payments,              // non-empty
    Some(change_script),    // ensures fee == needed_fee exactly
    Some(data),
    fee_per_vbyte,
).unwrap();

// The real transaction includes the ~25-vbyte OP_RETURN output
let real_vbytes = st.transaction().vsize() as u64;
// Fee committed = fee_per_vbyte * vbytes computed WITHOUT the OP_RETURN
let committed_fee = st.fee();
// Achieved fee rate is strictly below fee_per_vbyte:
assert!(committed_fee < fee_per_vbyte * real_vbytes);
// If committed_fee < DEFAULT_MIN_RELAY_TX_FEE * real_vbytes / 1000,
// the transaction is non-standard and will not relay.
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
