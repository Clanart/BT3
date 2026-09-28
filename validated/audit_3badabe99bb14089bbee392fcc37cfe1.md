### Title
`SignableTransaction::new` computes fee and weight from `payments` only, ignoring the OP_RETURN data output and `tx_outs`, so constructed transactions can underpay the minimum relay fee or exceed standard weight — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the auction rounding issue (a total computed one way while the parts are paid another way, making the operation fail), `SignableTransaction::new` sizes/fees the transaction using the `payments` slice rather than the actual `tx_outs` vector that includes the pushed `OP_RETURN` output. The "total" (real serialized transaction) is larger than the "sum" the fee/weight accounting assumed, so the resulting transaction can pay an effective feerate below the caller's requested rate — and potentially below `DEFAULT_MIN_RELAY_TX_FEE` — causing relay/settlement failure.

### Finding Description
In `SignableTransaction::new`:

1. `tx_outs` is built from `payments`, then an `OP_RETURN` output is appended when `data` is present [1](#0-0) .
2. Weight/vbytes are then computed with `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, which builds a dummy transaction whose outputs come only from `payments` — the OP_RETURN output is not counted [2](#0-1) .
3. The minimum-fee check `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` and the `NotEnoughFunds` check both use this underestimated `vbytes`/`needed_fee` [3](#0-2) .
4. The change computation likewise re-derives vbytes from `payments` (`Some(&change)`), again ignoring the OP_RETURN output bytes [4](#0-3) .
5. The `TooLargeTransaction` check uses `weight` computed from the payment-only dummy tx [5](#0-4) .

The OP_RETURN output adds ~9 + len(data) bytes (up to ~89 bytes ≈ 89 vbytes) that are never charged for. `fee()` correctly reports `inputs - outputs` [6](#0-5) , but the actual feerate is `fee / real_vbytes`, which is below the requested `fee_per_vbyte` whenever `data` is supplied.

### Impact Explanation
A transaction built with `data` pays a lower effective feerate than intended. If `fee_per_vbyte` is near the minimum (e.g., 1 sat/vbyte), the real feerate can fall below `DEFAULT_MIN_RELAY_TX_FEE`, making the transaction unrelayable — the same failure shape as the auction that can never settle: the produced artifact cannot complete its function. Funds are not stolen, but spends carrying metadata can stall. With many inputs plus an OP_RETURN output, the tx can also exceed `MAX_STANDARD_TX_WEIGHT` undetected, producing a transaction no standard node will relay. Severity: Medium (availability of the produced transaction, no secret leakage).

### Likelihood Explanation
Reachable whenever `data` is supplied to `SignableTransaction::new`. The `data` parameter is intended for protocol-embedded metadata (e.g., `SerializedBatch`/settlement data), so any call path embedding data triggers the undercount. The `payments`-only sizing is unconditional, so the discrepancy is deterministic rather than rounding-dependent.

### Recommendation
Compute weight/vbytes from the actual `tx_outs` (or pass `&tx_outs`-equivalent script/value pairs into `calculate_weight_vbytes`) after the OP_RETURN output is appended, both for the no-change and change branches, and perform the `TooLowFee`, `NotEnoughFunds`, and `TooLargeTransaction` checks against those final values. Alternatively, move OP_RETURN addition before size estimation and reuse a single helper that builds the full dummy tx from the real outputs.

### Proof of Concept
```rust
// In a test for networks/bitcoin/src/wallet/send.rs
let inputs = vec![/* one ReceivedOutput of value = DUST + needed */];
let payments = &[ (p2tr_script_buf(some_key), 546) ];
let data = Some(vec![0u8; 80]); // max allowed

// Request 1 sat/vbyte: minimum allowed feerate.
let stx = SignableTransaction::new(inputs, payments, None, data, 1).unwrap();

// Real vsize includes the OP_RETURN output; needed_fee was computed without it.
let real_vbytes = stx.transaction().vsize() as u64;
let paid = stx.fee();
assert!(paid < real_vbytes); // effective feerate < 1 sat/vB -> below min relay fee
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L137-141)
```rust
  /// Returns the fee this transaction will use.
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L188-202)
```rust
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();

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

**File:** networks/bitcoin/src/wallet/send.rs (L211-221)
```rust
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L225-234)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
