### Title
`SignableTransaction::new` omits the OP_RETURN `data` output from weight/fee calculation, so transactions carrying data underpay their intended fee rate - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` appends an OP_RETURN output carrying `data` to `tx_outs` (lines 194-202), but computes the transaction weight and required fee via `calculate_weight_vbytes(tx_ins.len(), payments, ...)` (lines 204 and 226), which builds its weight-estimation transaction solely from `payments` — never from the actual `tx_outs`. The up-to-83-byte OP_RETURN output is therefore invisible to the fee calculation, so any transaction that includes `data` pays a lower effective fee rate than the `fee_per_vbyte` the caller specified, and the `TooLowFee` check can pass on an underestimated vbytes figure. Analogous to the Kyber report (an intended code path exists yet the required input — here, the OP_RETURN output — never reaches the calculation), the `data` path's effect on the transaction is never accounted for downstream.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`):

- `tx_outs` is built from `payments`, then an OP_RETURN `TxOut` is pushed when `data` is `Some` (lines 187-202).
- Weight is then computed as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204). Inside `calculate_weight_vbytes` (lines 62-127), the dummy transaction's outputs are `payments.map(...)` plus an optional change output — the OP_RETURN output is not included.
- `needed_fee = fee_per_vbyte * vbytes` (line 206) and the minimum-fee check `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` (line 211) both use this underestimated `vbytes`.
- The change branch repeats the same underestimation: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (line 226).

A `data` payload of up to 80 bytes adds ~9-10 witness-free bytes of script plus the 8-byte value and 1-byte length to the real transaction — roughly 90+ weight units (~23 vbytes) omitted from the estimate. The resulting transaction is larger than estimated, so `fee() = sum(prevouts) - sum(outputs)` (lines 138-141) yields a fee below `fee_per_vbyte * actual_vbytes`. If `fee_per_vbyte` was chosen near the relay minimum, the signed transaction can fall under the default minimum relay fee and be rejected by the Bitcoin network even though `new` returned `Ok`.

### Impact Explanation
Transactions created with a non-`None` `data` payload systematically underpay the requested fee rate. In the worst case (a low `fee_per_vbyte` and a large payload), the signed transaction is below `DEFAULT_MIN_RELAY_TX_FEE` for its true size and cannot be relayed, stalling the spend; the threshold-signing ceremony completes and produces a transaction the network will not accept. Because the inputs remain locked to that transaction plan until a re-attempt with a corrected fee is coordinated, funds reported as sendable are effectively unspendable under the constructed transaction.

### Likelihood Explanation
Any `SignableTransaction::new` call with `data: Some(_)` triggers the miscalculation deterministically — it is not probabilistic or attacker-timing-dependent. Whether it becomes an outright relay failure depends on the chosen `fee_per_vbyte` relative to the relay minimum and the payload size; the fee-rate shortfall itself always occurs. The `data` parameter is part of the public transaction-construction API, so no privileged position is required to reach the path.

### Recommendation
Include the OP_RETURN output in the weight estimate. Pass the fully-formed `tx_outs` (or a flag/length for the data output) into `calculate_weight_vbytes` rather than `payments` alone, e.g. build the estimation transaction's outputs from the actual `tx_outs` vector plus the optional change output, at both call sites (lines 204 and 226). Alternatively, construct the real `Transaction` once and compute weight/vbytes directly from it.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// Conceptual PoC: construct a SignableTransaction with `data` and compare
// the estimated vbytes against the real transaction's vsize.

let data = vec![0u8; 80]; // max allowed payload
let stx = SignableTransaction::new(
    inputs,                       // non-empty Vec<ReceivedOutput>
    &payments,                    // non-empty payments
    None,                         // no change
    Some(data.clone()),           // OP_RETURN output is appended to tx_outs
    fee_per_vbyte,                // chosen near relay minimum
).unwrap();

// Internal estimate used for needed_fee omitted the OP_RETURN:
//   calculate_weight_vbytes(inputs, payments, None)
// Real vsize:
let real_tx = stx.transaction();
let real_vbytes = real_tx.vsize() as u64; // includes the ~90-byte OP_RETURN output
// real_vbytes > vbytes_estimate, so:
//   stx.fee() < fee_per_vbyte * real_vbytes
// and if fee_per_vbyte * real_vbytes < DEFAULT_MIN_RELAY_TX_FEE * real_vbytes / 1000
// passes the check yet the broadcast TX is below the minimum relay fee.
```

Relevant code: `networks/bitcoin/src/wallet/send.rs:194-202` (OP_RETURN appended to `tx_outs`), `send.rs:204-226` (weight computed from `payments` only), `send.rs:85-99` (`calculate_weight_vbytes` builds outputs only from `payments` and optional change).