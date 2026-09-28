### Title
`SignableTransaction` fee calculation omits the OP_RETURN output, producing transactions with a lower fee rate than requested - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The Derby report describes a metric (`exchangeRate()`) that is structurally unable to reflect the real quantity it should measure, causing the downstream derived value (`priceDiff`, and therefore rewards) to be permanently zero. The analog in Serai's in-scope code is `SignableTransaction::new`'s fee/vsize metric: `calculate_weight_vbytes` is invoked with `payments` — which never includes the OP_RETURN output appended to `tx_outs` — so `needed_fee` is computed for a smaller transaction than the one actually built and signed. The measured size is structurally incapable of reflecting the real transaction, exactly as the Aave exchange rate is structurally incapable of reflecting pool performance.

### Finding Description
In `SignableTransaction::new`, a data-bearing output is pushed to `tx_outs` before the weight/vbytes calculation:

- `send.rs:194-202` pushes `TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) }` onto `tx_outs` when `data` is `Some`.
- `send.rs:204` then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — passing `payments`, not the actual output list — so the OP_RETURN output (up to 80 bytes of data plus ~11 bytes of output overhead) is excluded from the computed `weight`/`vbytes`.
- `send.rs:206` derives `needed_fee = fee_per_vbyte * vbytes` from that under-measured vsize.
- `send.rs:225-227` repeats the same mistake for the change-including variant (`calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`), so `fee_with_change` is under-measured as well, which also inflates the change amount pushed at `send.rs:230`.

Additionally, the minimum-relay-fee gate at `send.rs:211` (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) is validated against the under-measured `vbytes`, so a transaction that genuinely fails the network's minimum relay fee can pass this check.

### Impact Explanation
A `SignableTransaction` built with `data: Some(...)` pays an effective fee rate strictly below the caller-specified `fee_per_vbyte`, because the fixed satoshi fee `needed_fee` is spread over a larger real vsize. In the worst case — a caller selecting a fee at/near the minimum relay rate with a large OP_RETURN — the signed transaction's true fee rate falls below `DEFAULT_MIN_RELAY_TX_FEE` and will be rejected by standard Bitcoin mempool policy, leaving the threshold wallet's outputs unspendable until a new transaction is constructed and re-signed. This is a deterministic miscalculation in the transaction-construction math, not an edge case requiring adversarial validators.

### Likelihood Explanation
Any caller invoking `SignableTransaction::new` with `data.is_some()` triggers it; `data` is arbitrary caller-controlled bytes (explicitly permitted up to 80 bytes at `send.rs:171-173`). The mispricing is proportional to the data length and always occurs — no race or adversary required.

### Recommendation
Compute `weight`/`vbytes` from the actual output list. Either call `calculate_weight_vbytes` after `tx_outs` is fully populated (passing `tx_outs`-equivalent data including the OP_RETURN output and change), or extend `calculate_weight_vbytes` to take `data: Option<&[u8]>` and include the OP_RETURN output in the mock transaction. Apply the same fix to the change branch so `fee_with_change` reflects the real transaction, and re-check the minimum relay fee against the final, complete vsize.

### Proof of Concept
```rust
// Conceptual reproduction against networks/bitcoin/src/wallet/send.rs
let data = vec![0u8; 80]; // max permitted by the TooMuchData check
let tx = SignableTransaction::new(
    inputs,                 // any valid ReceivedOutput set
    payments,               // any payments summing < inputs
    Some(change_script),    // or None
    Some(data.clone()),
    fee_per_vbyte,          // e.g. exactly the min relay rate
).unwrap();

// The real serialized tx includes the ~91-byte OP_RETURN output,
// yet needed_fee was computed from vbytes that excluded it.
assert!(tx.needed_fee() < fee_per_vbyte * real_vbytes(&tx));
// Therefore actual fee rate = tx.fee() / real_vbytes < fee_per_vbyte.
// With fee_per_vbyte at the minimum relay rate, the signed TX
// violates DEFAULT_MIN_RELAY_TX_FEE despite the TooLowFee check passing.
```

The root cause is visible directly at `send.rs:194-206` and `send.rs:225-227`: `tx_outs` already contains the OP_RETURN output when `calculate_weight_vbytes` is called, but the function receives only `payments`, so the measured transaction is structurally smaller than the one signed — the same "measurement model that cannot capture the real quantity" bug class as the Derby finding.