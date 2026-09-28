### Title
OP_RETURN data output excluded from fee/weight calculation, underpaying the fee and risking unrelayable transactions — ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` appends an `OP_RETURN` output to `tx_outs` when `data` is specified, but then computes the transaction weight/vbytes — and therefore `needed_fee` — from `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which only accounts for the payment outputs. The serialized OP_RETURN output (~11 + len(data) bytes, up to ~91 vbytes of weight) is never counted. The analog to "the protocol does not return all of the rewards": value accounting in the construction path ignores part of what the transaction actually carries, so the fee the user is quoted (`needed_fee`, `fee()` semantics) does not match the true transaction size.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs:194-204`, the OP_RETURN output is pushed to `tx_outs` before the weight computation:

```rust
// networks/bitcoin/src/wallet/send.rs:194-204
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...)
  })
}
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` (lines 62-127) builds a dummy `Transaction` containing only `payments` (and optionally `change`) — it has no parameter for data outputs, so the OP_RETURN bytes are simply absent from the weight estimate. The same omission affects the change branch at lines 224-227, where `fee_with_change` is computed with `payments` and `Some(&change)` but still no data output.

Consequences:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) is computed against a `vbytes` that excludes up to ~91 bytes of OP_RETURN, so the realized feerate is `fee / actual_vsize < fee_per_vbyte`.
- The minimum-relay-fee check at line 211 (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) uses the same underestimated `vbytes`, so a transaction whose *actual* feerate falls below the relay minimum can pass this guard.
- The `MAX_STANDARD_TX_WEIGHT` check at line 241 uses `weight` that also excludes the data output, letting a transaction exceed the standard weight limit while passing validation.
- Change is computed as `input_sat - payment_sat - fee_with_change` (line 228); the change output is correctly valued, but the overall size estimate is wrong, so `needed_fee()`/`fee()` report a rate that the mined transaction does not achieve.

A caller requesting `fee_per_vbyte` at or near the relay minimum while attaching near-maximal `data` produces a signed transaction whose effective feerate is below `DEFAULT_MIN_RELAY_TX_FEE`: it will not be relayed/confirmed, while the builder believes it paid a valid fee.

### Impact Explanation
A transaction built with `data` (used by the processor to attach `InInstruction` payloads to Bitcoin sends, per `processor/src/networks/bitcoin.rs` extract_serai_data usage) can be signed and broadcast with an effective feerate below what was requested — potentially below the mempool's minimum relay feerate. The funds in the inputs are then locked in a transaction that cannot confirm and may not even propagate, and `needed_fee()`/`fee()` misreport what was paid. This is the same class as the referenced finding: the protocol's accounting of what the user pays/receives omits part of the actual value flow (here, the bytes the user is charged for are fewer than the bytes the network charges against).

### Likelihood Explanation
Reachable by any caller of the public `SignableTransaction::new` API who supplies `Some(data)` along with a low `fee_per_vbyte`. The miscalculation is deterministic — every invocation with `data.is_some()` undercounts the weight. Severity is bounded because the processor currently passes `None` for `data` in `make_signable_transaction` (`processor/src/networks/bitcoin.rs:450`), so the buggy path requires an integrator (or future in-repo caller) to attach data; hence Medium rather than High.

### Recommendation
Include the OP_RETURN output in the weight estimate. Either pass the already-built `tx_outs` (or `payments` plus the data output) into `calculate_weight_vbytes`, or add a `data_len: usize` parameter that pushes a dummy `TxOut { value: Amount::ZERO, script_pubkey: <OP_RETURN of that length> }` into the dummy transaction before calling `tx.weight()`. Apply the same fix to both call sites (lines 204 and 225-226) so `needed_fee`, the min-relay check, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the real transaction.

### Proof of Concept
```rust
// 80-byte data output adds ~91 serialized bytes, never counted
let inputs = vec![received_output]; // value V
let data = vec![0u8; 80];
let tx = SignableTransaction::new(
  inputs.clone(), &[(addr(), 1000)], None, Some(data.clone()), 1, // 1 sat/vbyte
).unwrap();

// needed_fee was computed for a tx *without* the OP_RETURN output.
// The real tx.vsize() is larger, so:
//   tx.fee() as f64 / tx.vsize() as f64  <  1.0
assert!(tx.fee() < u64::try_from(tx.vsize()).unwrap());
// i.e., effective feerate < requested feerate; at the relay boundary this
// produces a transaction below DEFAULT_MIN_RELAY_TX_FEE per actual vbyte.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** networks/bitcoin/src/wallet/send.rs (L194-212)
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
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
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
