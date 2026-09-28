### Title
Fee and weight calculation omits the OP_RETURN data output, producing an underpriced/over-weight transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes a function that computes an average over two values expressed in different units/directions, returning a wrong result. The analog in Serai's in-scope code is `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`: the transaction's fee, minimum-fee check, and standardness weight check are all computed from a model transaction that excludes an output which is actually included in the final transaction — the `OP_RETURN` data output. The measured object and the constructed object are not the same thing, so every derived quantity is wrong.

### Finding Description
`SignableTransaction::new` accepts an optional `data` payload and pushes a zero-value `OP_RETURN` output onto `tx_outs` (the real transaction's outputs) at `networks/bitcoin/src/wallet/send.rs:193-202`. However, the subsequent call to `calculate_weight_vbytes` at `send.rs:204` passes `payments` — not `tx_outs` — so the returned `weight` and `vbytes` describe a transaction *without* the data output:

```rust
// send.rs:193-206
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

Three derived quantities are therefore incorrect whenever `data` is `Some`:

1. `needed_fee` undercounts by `fee_per_vbyte * vbytes_of_OP_RETURN_output` (an ~80-byte payload costs ~90 vbytes that are never charged).
2. The minimum-relay-fee check at `send.rs:211` compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the too-small `vbytes`, so a transaction can pass the check while its actual sat/vbyte rate is below the relay minimum.
3. The `MAX_STANDARD_TX_WEIGHT` check at `send.rs:241` uses the too-small `weight`, so a transaction near the 400,000 WU limit can pass while the real serialized transaction exceeds the standardness limit and will not be relayed.

Notably, the change-output path at `send.rs:225-226` correctly recomputes weight with the change output included, showing the intent was to account for every output — the data output was simply missed.

### Impact Explanation
An unprivileged caller supplies `data` (up to 80 bytes) to `SignableTransaction::new`. The resulting `SignableTransaction` is then signed by the FROST `TransactionMachine` and broadcast. Because the fee was computed on a smaller transaction than the one signed, the actual fee rate is lower than `fee_per_vbyte`, and can fall below the network minimum relay fee even though `TooLowFee` was not raised. In the worst case the signed transaction exceeds `MAX_STANDARD_TX_WEIGHT` despite the internal check passing, making it non-standard and unrelayable — the inputs are committed to a transaction that cannot confirm. This matches the report's impact shape: an incorrect computed value silently propagates into a downstream decision.

### Likelihood Explanation
The bug triggers on every call with `data.is_some()`: the miscalculation is deterministic, not probabilistic. Whether the transaction fails in practice depends on how close the undervalue pushes the actual fee rate toward the relay floor or the weight toward the standardness cap — but `needed_fee()` verifiably returns a value that does not correspond to the transaction that will be signed. Reachability is via a public constructor argument, requiring no privileged position.

### Recommendation
Build the output list used for sizing from the same `tx_outs` vector that goes into the final transaction, or pass the data output into `calculate_weight_vbytes` (e.g., extend its signature to take the full `Vec<TxOut>`/`&[TxOut]`). Then recompute `weight`, `vbytes`, `needed_fee`, the min-relay check, and the weight-cap check against the complete output set. Add a test asserting `SignableTransaction::new(..., Some(vec![0; 80]), ...)` yields `needed_fee` consistent with `tx.vsize()` of the produced transaction.

### Proof of Concept
```rust
// Conceptual: construct two transactions differing only by `data`
let no_data = SignableTransaction::new(inputs.clone(), &payments, None, None, FEE).unwrap();
let with_data =
  SignableTransaction::new(inputs, &payments, None, Some(vec![0u8; 80]), FEE).unwrap();

// The data transaction is ~90 vbytes larger, yet is charged identically
assert_eq!(no_data.needed_fee(), with_data.needed_fee()); // BUG: should differ
// Its actual fee rate is below FEE
assert!(with_data.fee() < FEE * u64::try_from(with_data.transaction().vsize()).unwrap());
```

Additionally, a transaction sized to just below `MAX_STANDARD_TX_WEIGHT` without the data output will pass the check at `send.rs:241` while the real transaction exceeds it. [1](#0-0) [2](#0-1)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L193-213)
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
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-243)
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
    }
```
