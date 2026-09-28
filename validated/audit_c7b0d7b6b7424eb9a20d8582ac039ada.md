### Title
SignableTransaction adds the OP_RETURN data output after fee/size accounting, leaving `needed_fee`/`vbytes` stale — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` builds `tx_outs` including a caller-supplied OP_RETURN data output, but computes `weight`/`vbytes`/`needed_fee` from `payments` only via `calculate_weight_vbytes(tx_ins.len(), payments, ...)`. The data output — which is never a "payment" and never a "change" — is invisible to the fee accounting and to the minimum-relay-fee check. The resulting transaction is larger than what was paid for, paying an effective fee rate below `fee_per_vbyte` and potentially below Bitcoin's minimum relay fee.

### Finding Description
In `SignableTransaction::new`:

1. The OP_RETURN output is pushed to `tx_outs` when `data` is supplied (`send.rs` lines 193–202).
2. Fee/size are then computed as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204) and, in the change path, `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (lines 225–226). `calculate_weight_vbytes` (lines 62–127) reconstructs a transaction whose `output` list contains only `payments` plus an optional change output — the `data` output is never included.
3. `needed_fee = fee_per_vbyte * vbytes` (lines 206, 227–232) and the `TooLowFee` check (line 211) are therefore evaluated against a vsize that omits the data output entirely — a non-witness output of up to ~93 bytes (8-byte amount + ~85-byte OP_RETURN script for the 80-byte maximum `data`), i.e. up to ~93 missing vbytes.
4. When a change output is produced, the change amount is `input_sat - payment_sat - fee_with_change` (line 228), so the signed transaction pays exactly `fee_with_change` over a transaction that is actually larger — an underpriced transaction at a lower sat/vbyte than requested, and possibly below `DEFAULT_MIN_RELAY_TX_FEE` for the real vsize.

This is the same bug class as the external report: a state/accounting value (`needed_fee`, derived `vbytes`/`weight`) is not updated when a new component (the OP_RETURN output) is added, so all downstream logic (minimum-fee gate, change amount, caller-visible `needed_fee()`) operates on a stale figure. An unprivileged caller reaches this path through the public `data: Option<Vec<u8>>` parameter of `SignableTransaction::new` (up to 80 bytes, line 171) — an ordinary transaction-data input, not a privileged action.

### Impact Explanation
Any caller that constructs a transaction with an OP_RETURN payload and a change output produces a transaction paying a lower effective fee rate than the requested `fee_per_vbyte`. In the worst case — where `needed_fee` barely passes the `TooLowFee` check against the underestimated vsize — the signed transaction is below the real minimum relay fee and is rejected by relay/mempool policy, stalling the spend. Because the change output already consumed all leftover funds (`input_sat - payment_sat - fee_with_change`), the transaction cannot be bumped by value adjustment without rebuilding; the inputs are locked until a corrected transaction is signed. Additionally, `needed_fee()` returns a value that is not the actual fee paid (`fee()` computes the true `inputs - outputs` sum), so callers accounting against `needed_fee` misattribute the difference.

### Likelihood Explanation
Reachable whenever `data` is non-empty: the `data` bytes are public caller input, gated only by an 80-byte length check. The fee underestimate is deterministic and proportional to the data length (up to ~93 vbytes of unaccounted weight). The relay-rejection case requires `fee_per_vbyte` to be near the minimum relay fee, which is the common operating regime for fee-sensitive batching. Notably, `processor/src/networks/bitcoin.rs` currently passes `None` for `data`, but `SignableTransaction` is the public wallet API and the parameter exists precisely to be used.

### Recommendation
Include the OP_RETURN output in the weight/vsize calculation. Either move the `data` output into the `payments`-style list passed to `calculate_weight_vbytes` (it is a zero-value output, so value math is unaffected — only weight matters), or add an explicit `data_len`/extra-output parameter so `calculate_weight_vbytes` builds the same output set that `new` will actually emit. The change-output path must also recompute with the data output present.

### Proof of Concept
```rust
// networks/bitcoin: conceptual PoC against SignableTransaction::new
let data = vec![0u8; 80]; // max allowed
let tx = SignableTransaction::new(
    vec![input],                // any ReceivedOutput
    &[(payment_script, 1000)],
    Some(change_script),        // force the change path
    Some(data),
    fee_per_vbyte,              // e.g. 1 (min relay)
).unwrap();

// tx.transaction().output includes a 3rd, zero-value OP_RETURN output,
// but needed_fee was computed as if it had only payment + change outputs.
let real_vsize = tx.transaction().vsize() as u64; // includes OP_RETURN output
assert!(tx.needed_fee() < fee_per_vbyte * real_vsize);
// If needed_fee == vbytes_estimate * 1 sat/vB, the real fee rate is
// needed_fee / real_vsize < 1 sat/vB => below DEFAULT_MIN_RELAY_TX_FEE.
```

The root cause is `SignableTransaction::new` at `networks/bitcoin/src/wallet/send.rs` lines 193–235: `tx_outs` gains an output (line 195) that the `weight`, `vbytes`, `needed_fee`, and `TooLowFee` state (lines 204–232) never accounts for.