### Title
Fee/weight estimation ignores the OP_RETURN data output, producing transactions that pay less than the requested rate and can fall below relay minimums despite sufficient funds - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the Archimedes report — where a flawed estimate of the required input (`get_dy` reversed) causes an unwind to revert even though the user holds more than enough OUSD — `SignableTransaction::new` computes the required fee from a transaction template that omits the OP_RETURN data output it then appends to the real transaction. The resulting `needed_fee` systematically underestimates the fee needed for the requested `fee_per_vbyte`, and the `TooLowFee` / `TooLargeTransaction` policy checks are evaluated against a smaller, incorrect weight. The wallet can therefore produce a transaction that is under the node's minimum relay fee or over the standard weight limit — unbroadcastable — even though the inputs fully cover a valid fee.

### Finding Description
`SignableTransaction::new` builds `tx_outs` from `payments` and then pushes an OP_RETURN `TxOut` carrying up to 80 bytes of caller-supplied `data` (lines 193-202). However, both weight/vbyte estimates are computed by `calculate_weight_vbytes(tx_ins.len(), payments, ...)` (lines 204, 225-226), which reconstructs a transaction containing **only the payment outputs** (lines 85-93) plus optionally change (lines 95-99). The OP_RETURN output — roughly `(8 value + 1 len + 1 opcode + 1 push + data.len())` non-witness bytes, i.e. ~11-92 bytes / ~44-368 WU / ~11-92 vbytes — is never included in the estimate.

Consequences:
1. `needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change` (line 227) are too low, so the signed transaction's effective fee rate is below the caller-requested rate.
2. The `TooLowFee` guard compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the underestimated `vbytes` (line 211). A transaction that just passes this check can actually sit below the minimum relay fee for its true size and be rejected by the network — the direct analog of "position cannot be unwound although enough OUSD is held": the inputs cover a valid fee, yet the produced transaction is unspendable/unrelayable.
3. The `MAX_STANDARD_TX_WEIGHT` check (line 241) uses the underestimated `weight`, so a transaction including a large data payload plus many payments can pass the check yet exceed the 400,000 WU standardness limit and be rejected.
4. When `change` is present, `input_sat.checked_sub(payment_sat + fee_with_change)` (line 228) decides whether change is created using the wrong fee; the sat value of change stays correct, but the decision boundary and reported `needed_fee` are wrong.

The bug is reachable by any caller of this public API who supplies `data` — the OP_RETURN output is part of the transaction data being signed — matching the "transaction data they cause to be signed" reachability class.

### Impact Explanation
A transaction produced by `SignableTransaction::new` with a `data` payload pays a lower effective fee rate than requested. At low fee rates the transaction falls under `DEFAULT_MIN_RELAY_TX_FEE` for its true size and is rejected from relay/mempool; at the size boundary it exceeds `MAX_STANDARD_TX_WEIGHT` and is non-standard. In both cases the resulting signed transaction cannot be broadcast despite the inputs holding more than enough funds, forcing re-signing rounds and potentially stalling payouts — analogous to the unwind reverting with `Not enough LvUSD in pool`. Additionally, `needed_fee()` reports an incorrect value to any caller that amortizes fees across payments.

### Likelihood Explanation
The miscalculation is deterministic: every call that passes `Some(data)` underestimates, because the OP_RETURN output is unconditionally absent from `calculate_weight_vbytes`'s template. It requires no adversarial timing or state, only a data-bearing send constructed near a fee-rate or weight boundary — precisely the "boundary miscalculation" profile of the reference bug.

### Recommendation
Include the data output in the weight estimation: either pass the already-built `tx_outs` (including the OP_RETURN output) into `calculate_weight_vbytes`, or construct the template transaction directly from `tx_outs` so the estimate always reflects the final transaction. Re-derive `vbytes`, `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check from that complete template.

### Proof of Concept
In `networks/bitcoin/src/wallet/send.rs`:

```rust
// OP_RETURN output appended to the real tx
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(PushBytesBuf::try_from(data).unwrap()),
  })
}

// ...but the fee/weight estimate is built only from `payments`
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
  Err(TransactionError::TooLowFee)?;
}
```

Construct `SignableTransaction::new(inputs, payments, change, Some(vec![0u8; 80]), fee_per_vbyte)` with `fee_per_vbyte` chosen just above the minimum relay rate for the *estimated* vbytes. The call succeeds and `needed_fee` passes the `TooLowFee` check, yet the actual transaction (which contains the extra ~92-byte OP_RETURN output) has an effective rate below `DEFAULT_MIN_RELAY_TX_FEE` and will be rejected by the network — an unbroadcastable transaction despite sufficient input funds. Similarly, choose inputs/payments so estimated `weight` is just under `MAX_STANDARD_TX_WEIGHT`; the added OP_RETURN weight pushes the real transaction over the standard limit while the check still passes. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** networks/bitcoin/src/wallet/send.rs (L224-242)
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
```
