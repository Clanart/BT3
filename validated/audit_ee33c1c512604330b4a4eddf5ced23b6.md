### Title
`SignableTransaction::new` computes `needed_fee` without the OP_RETURN data output, underpaying fees on attacker-influenced transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` under-accounts the transaction's weight/vbytes when a `data` payload is present: the OP_RETURN output is pushed onto `tx_outs` at line 195, but both calls to `calculate_weight_vbytes` (lines 204 and 226) pass only `payments`, so the OP_RETURN output's size is never included in `vbytes`/`weight`. The resulting `needed_fee` and the change amount are computed against a smaller transaction than the one actually signed and broadcast. This is the same class as the reference bug: the internal accounting (virtual size → fee → change) is based on a different value than what actually hits the wire.

### Finding Description
At `networks/bitcoin/src/wallet/send.rs:193-202`, if `data` is `Some`, an OP_RETURN output carrying up to 80 bytes of attacker-influenced data is appended to `tx_outs`. However:

- Line 204: `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — `payments` excludes the OP_RETURN output.
- Line 226: `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` — same omission, so `vbytes_with_change` and `fee_with_change` are also too low.
- Line 228-230: change is `input_sat - payment_sat - fee_with_change`, i.e. change is credited *more* sats than it should be (the missing fee for the OP_RETURN output is effectively absorbed into change), while the actual transaction is larger than accounted for. The real fee paid (`fee()`, lines 138-141: `sum(inputs) - sum(outputs)`) equals `needed_fee`, but spread over a larger vsize than `vbytes_with_change`, so the effective fee rate is strictly below `fee_per_vbyte`.

Additionally, the minimum-relay check at line 211 uses the underestimated `vbytes`, so a transaction can pass the check yet broadcast at a fee rate below `DEFAULT_MIN_RELAY_TX_FEE` once the OP_RETURN output is included (up to ~80+ bytes / ~20+ vbytes of unaccounted size for max-size data).

### Impact Explanation
A transaction built with `data` pays a lower effective fee rate than requested, and can fall below the minimum relay fee, causing the signed transaction to be rejected or stuck unconfirmed. The change output is also larger than intended (fee discrepancy is paid out of the fee budget into change), so `needed_fee` no longer reflects the fee rate the transaction actually achieves — analogous to `poolAmount` diverging from the true balance in the reference issue. Funds aren't directly stolen, but outputs routed through a transaction constructed this way may be unspendable/delayed until reconstructed.

### Likelihood Explanation
Reachable by an unprivileged party whenever `data` is set by externally supplied input (e.g. instruction data accompanying routed outputs). The discrepancy is proportional to the data length, so a maximum-size (80-byte) payload maximizes the fee-rate shortfall. Impact is limited to under-priced/stuck transactions rather than fund loss, so Medium.

### Recommendation
Include the OP_RETURN output in the weight/vsize calculation: build the output list passed to `calculate_weight_vbytes` from the fully assembled `tx_outs` (payments + OP_RETURN), or add the data output's serialized size to the computed weight in both the no-change and with-change calls before deriving `needed_fee`/`fee_with_change`.

### Proof of Concept
```rust
// In SignableTransaction::new (send.rs):
let data = vec![0u8; 80];
let tx = SignableTransaction::new(inputs, &payments, Some(change), Some(data.clone()), fee_per_vbyte)?;
// tx_outs contains an OP_RETURN output (~92 bytes serialized),
// but vbytes/needed_fee were computed from `payments` alone.
// Effective fee rate = needed_fee / actual_vsize < fee_per_vbyte,
// and actual_vsize - accounted_vsize grows with data.len().
assert!(tx.fee() == tx.needed_fee()); // fee paid equals underestimated value
```