### Title
Fee and weight are computed for a transaction that omits the OP_RETURN `data` output actually signed - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` appends the `data` OP_RETURN output to `tx_outs` before the fee/weight calculation, but `calculate_weight_vbytes` is only ever called with `payments` — never with the data output. The result mirrors the GPToke bug: a quantity (`needed_fee`, `weight`, `vbytes`) is derived from the wrong input — the transaction *without* the extra output — instead of the transaction actually constructed and signed.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` at lines 194–202. However, both calls to `calculate_weight_vbytes` (lines 204 and 226) pass `payments`, which does not include the data output:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` undercounts the real vsize by the size of the OP_RETURN output (up to ~85 bytes for the 80-byte data limit). The signed transaction therefore pays an *effective* fee rate below the caller-specified `fee_per_vbyte`. If `fee_per_vbyte` was at/near the relay minimum, the broadcast transaction can be non-relayable even though the `TooLowFee` check passed.
2. The `weight > MAX_STANDARD_TX_WEIGHT` check at line 241 uses `weight`/`weight_with_change` computed without the data output, so a transaction that is actually over the standard-weight limit can be accepted and signed — producing a transaction Bitcoin nodes will reject.
3. The change-amount path (line 228) computes `fee_with_change` on the same undersized weight, so the change output value is derived from a fee that does not correspond to the final transaction.

The attacker-reachable surface: `data` is a public caller input to `SignableTransaction::new`, and the resulting transaction is what the threshold signing machines (`TransactionSignMachine::sign`, lines 373–397) actually sighash and sign via `SighashCache::new(&self.tx.tx)` — i.e., the real, larger transaction — while fee/weight validation was done against a different, smaller transaction.

### Impact Explanation
The library attests a fee (`needed_fee`) and a size bound (`MAX_STANDARD_TX_WEIGHT`) for a transaction different from the one signed. A transaction can be produced that (a) pays a lower effective fee rate than requested — potentially below `DEFAULT_MIN_RELAY_TX_FEE` once the omitted output's weight is accounted for, making it non-propagating/non-minable — and (b) can exceed `MAX_STANDARD_TX_WEIGHT` while passing the check, yielding an unbroadcastable transaction. For a threshold wallet this means funds committed as inputs are locked in a transaction the network rejects, and any downstream accounting relying on `needed_fee()`/`fee()` is wrong. This is the same structural defect as GPToke: the formula's variable (`vbytes`/`weight`) is computed from the wrong operand.

### Likelihood Explanation
Triggered whenever `data` is `Some` — an entirely public input. Every such call miscomputes fee and weight; the severity of the outcome depends on `fee_per_vbyte` and how close the transaction is to the weight/relay-minimum boundaries. Medium likelihood of a stuck/non-standard transaction in boundary conditions; the miscalculation itself is deterministic.

### Recommendation
Compute weight/vbytes over the same output set that will be signed. Pass the data output into `calculate_weight_vbytes` (e.g., build the full `tx_outs` including the OP_RETURN before fee estimation, or add an explicit `data_len` parameter to the weight function so `calculate_weight_vbytes(tx_ins.len(), payments, change, data_len)` accounts for the OP_RETURN output's serialized size). Then re-run the `TooLowFee`, `NotEnoughFunds`, and `MAX_STANDARD_TX_WEIGHT` checks against the corrected values, and derive the change amount from `fee_with_change` computed over the complete output set.

### Proof of Concept
```rust
// Conceptual: SignableTransaction::new(inputs, payments, change, Some(data), fee)
// 1. tx_outs gains a OP_RETURN output of ~10+data.len() serialized bytes (send.rs:194-202)
// 2. calculate_weight_vbytes(tx_ins.len(), payments, None) at send.rs:204 builds a
//    Transaction whose `output` vec lacks that OP_RETURN output
// 3. needed_fee = fee_per_vbyte * vbytes(smaller_tx)
// 4. The signed tx (send.rs:373-397) commits to the larger tx_outs
// Assert: tx.weight() > weight used for the MAX_STANDARD_TX_WEIGHT check,
// and fee()/tx.vsize() < fee_per_vbyte whenever data.is_some()
```

Concretely, for any `data` of length `d`, the real transaction is `(d + overhead)` bytes larger than the one measured, so the effective fee rate is `fee() / actual_vsize < fee_per_vbyte`, and a transaction at the `MAX_STANDARD_TX_WEIGHT` boundary passes validation while being non-standard.