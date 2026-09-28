### Title
OP_RETURN data output excluded from fee/weight accounting yields transactions paying below the intended feerate, potentially unrelayable - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` builds `tx_outs` from `payments` plus an optional OP_RETURN `data` output, but both calls to `calculate_weight_vbytes` pass `payments` — not `tx_outs`. The size and weight of the OP_RETURN output (up to ~92 vbytes for 80 bytes of data) is never included in `needed_fee`, in the `TooLowFee` minimum-relay check, or in the `MAX_STANDARD_TX_WEIGHT` check. The constructed transaction therefore pays `fee_per_vbyte` only for the outputs that existed before `data` was appended, and the change output is made larger than it should be, silently masking the shortfall.

### Finding Description
In `SignableTransaction::new`:

1. `tx_outs` is initialized from `payments` (lines 188–191) and then the OP_RETURN output is pushed onto it when `data` is `Some` (lines 194–202).
2. The initial weight/vbytes computation uses `payments`, not `tx_outs`: `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204).
3. `needed_fee = fee_per_vbyte * vbytes` (line 206) and the minimum-relay-fee check (lines 211–213) are therefore evaluated against a transaction missing the OP_RETURN output.
4. When a change output is added, `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (lines 225–227) again omits the OP_RETURN output, and `needed_fee` is overwritten with this still-too-low value (line 232).
5. The final `weight > MAX_STANDARD_TX_WEIGHT` check (line 241) also uses the weight without the data output.
6. `fee()` (lines 138–141) computes the actual paid fee as `sum(prevouts) - sum(outputs)`, which equals the under-estimated `needed_fee` — the discrepancy is never detected because `needed_fee()` and `fee()` are consistent with each other while both understate the true transaction size.

Analogous to the reference bug — where an adjustment (PnL) already applied in one step was erroneously re-applied/removed in the final accounting — here the cost of an output actually committed to `tx.output` is omitted from the accounting step entirely, and the leftover is folded into change, so the accounting "balances" while being wrong.

### Impact Explanation
- The transaction's effective feerate is `needed_fee / actual_vbytes`, strictly less than `fee_per_vbyte`. For small transactions (1–2 inputs, few payments) and a maximal `data` payload (~80 bytes → ~92 extra vbytes), the real feerate can be ~35–40% below the requested rate.
- If `fee_per_vbyte` is at or near the minimum relay feerate, the actual feerate falls below `DEFAULT_MIN_RELAY_TX_FEE` even though the `TooLowFee` check passed — the transaction is rejected by node mempools, so the spend cannot be relayed or confirmed. The signer set produces a valid signature over a transaction the network will not accept, a liveness failure of the spend path.
- The `MAX_STANDARD_TX_WEIGHT` check can likewise pass for a transaction that is non-standard once the real output set is counted.
- Because `data` content is derived from user-supplied instructions that get embedded in forwarded/aggregation transactions, an unprivileged depositor controls the presence and size of the OP_RETURN output.

### Likelihood Explanation
Requires a `SignableTransaction` constructed with `data: Some(...)` and a `fee_per_vbyte` close to the minimum relay rate, or simply feerate-sensitive timing. Any such transaction is deterministically under-priced; no race or privileged position is needed. Under-payment of the targeted rate occurs on every transaction carrying data, so the misaccounting is guaranteed whenever data is present; the full DoS requires the fee rate to be near the relay floor.

### Recommendation
- Pass the full output set to `calculate_weight_vbytes`. Change its signature to accept `&[TxOut]` (or compute weight after fully assembling `tx_outs`, including the OP_RETURN and change outputs), and base `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check on that complete output set.
- When computing `fee_with_change`, include the data output as well so the change amount `input_sat - payment_sat - fee_with_change` remains consistent.
- Alternatively, assert post-construction that `fee() >= fee_per_vbyte * actual_vsize` and rebuild/Err otherwise.

### Proof of Concept
1. Construct a `SignableTransaction` with one input of, e.g., 10,000 sats, one payment of `DUST` sats, no change, and `data = Some(vec![0u8; 80])`, with `fee_per_vbyte` chosen so `needed_fee` equals exactly the minimum relay fee for the computed `vbytes` (passes line 211).
2. Observe `tx.output` contains two outputs: the payment and the ~83-byte OP_RETURN script (~92 output bytes ≈ 92 vbytes).
3. Compute `actual_vsize = signed_tx.vsize()`; then `fee() = 10000 - 546` while `fee() / actual_vsize < 1 sat/vB`, i.e., below `DEFAULT_MIN_RELAY_TX_FEE` despite `TooLowFee` not being returned.
4. `send_raw_transaction` on a standard node rejects the transaction with a min-fee-not-met error, while `SignableTransaction::new` reported success — demonstrating the accounting mismatch between outputs committed in `tx.output` and the fee/weight they were charged for.