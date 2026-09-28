### Title
`SignableTransaction::new` computes weight/fee from `payments` only, ignoring the OP_RETURN data output — signed transactions underpay and may be rejected as non-standard - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The Platypus exploit class is a computed value (the swap's `actualToAmount`/slippage) derived from accounting state (`cash`, `liability`) that no longer reflects the real intermediate state after prior operations. The analog in Serai's Bitcoin wallet is the same shape: `SignableTransaction::new` appends the OP_RETURN data output to `tx_outs`, but computes `weight`, `vbytes`, `needed_fee`, `fee_with_change`, and the `TooLargeTransaction` check from `calculate_weight_vbytes(tx_ins.len(), payments, …)`, which is passed `payments` rather than `tx_outs`. The data output therefore contributes zero weight, zero fee, and zero to the standardness bound, so the finalized transaction is larger than what was priced.

### Finding Description
- `tx_outs` gets the OP_RETURN output pushed at send.rs:194-202 before the fee computation.
- At send.rs:204 and send.rs:225-227, `calculate_weight_vbytes` is called with `payments`, not `tx_outs`, so the ~10–90 extra bytes of the data output are never included in `vbytes` / `vbytes_with_change`.
- `needed_fee = fee_per_vbyte * vbytes` (send.rs:206,227) is therefore an underestimate; the actual transaction relays at an effective fee rate strictly below `fee_per_vbyte`.
- `TooLowFee` is checked against this underestimated `vbytes` (send.rs:211), and `TooLargeTransaction` is checked against the underestimated `weight` (send.rs:241).
- Change computation at send.rs:228 also uses `fee_with_change` which excludes the data output's weight.
- Inputs use `sequence: Sequence::MAX` (send.rs:79,182), so the resulting transaction is not replaceable (no RBF): an underpriced transaction stuck below the mempool minimum cannot be fee-bumped in place and can only be resolved by re-signing a conflicting spend.

### Impact Explanation
When `data` is present, the produced transaction's true fee rate is lower than requested and, at fee rates near the minimum, can fall below `DEFAULT_MIN_RELAY_TX_FEE` once the uncounted OP_RETURN vsize is included. Nodes then reject the signed transaction, leaving the consumed outputs unspendable through this transaction; recovery requires coordinating a new threshold signature over a corrected transaction. This is a miscalculation in the transaction-construction formula reachable purely through signed transaction data (Medium severity: funds temporarily immobilized, not stolen).

### Likelihood Explanation
Triggered whenever `SignableTransaction::new` is invoked with `Some(data)` — up to 80 bytes of uncounted output, plus output overhead (~100 vbytes unpriced). The miss becomes an actual relay failure only when `fee_per_vbyte` is at/near the protocol minimum; at higher rates it merely silently underpays. No signature forgery or key exposure is possible — impact is availability of the funds, not confidentiality.

### Recommendation
Pass `&tx_outs` (post-OP_RETURN) — or equivalently `payments` plus the data output — into both `calculate_weight_vbytes` calls in `SignableTransaction::new`, and perform the `MAX_STANDARD_TX_WEIGHT` check on the weight of the actual final `tx` (e.g., `self.tx.weight()`) rather than the estimate.

### Proof of Concept
```rust
// Construct a SignableTransaction carrying OP_RETURN data:
let tx = SignableTransaction::new(
    vec![output],                       // one input
    &[],                                // no payments
    Some(p2tr_script_buf(key).unwrap()),// change
    Some(vec![0u8; 80]),                // max data
    fee_per_vbyte,                      // = minimum relay rate
).unwrap();

// tx.needed_fee() == fee_per_vbyte * vbytes(no OP_RETURN)
// but tx.transaction().vsize() includes the ~90 extra bytes of the OP_RETURN
// output, so actual fee rate = needed_fee / real_vsize < fee_per_vbyte,
// and can be < DEFAULT_MIN_RELAY_TX_FEE -> rejected by Bitcoin nodes,
// with Sequence::MAX making the transaction non-replaceable.
```

Caveat: whether the `data` field is populated from bytes an unprivileged external user controls (e.g., instruction data) or only from coordinator-fixed protocol values could not be fully confirmed within the available search iterations; the miscalculation is unconditional whenever `data` is set, but the externally-driven likelihood depends on that data flow.