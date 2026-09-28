### Title
OP_RETURN data output excluded from fee/weight estimation, yielding an under-priced transaction the network will not relay - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` adds a user-controlled OP_RETURN output (up to 80 bytes of data) to `tx_outs`, but computes the transaction's vsize/weight — and therefore `needed_fee`, the minimum-relay check, and the `MAX_STANDARD_TX_WEIGHT` check — from a template built only from `payments` and `change`, never including the OP_RETURN output. The signed transaction is therefore larger than estimated while paying only the estimated fee, so its effective fee rate is lower than the caller-specified `fee_per_vbyte` — potentially below the minimum relay fee — and its true weight may exceed the standardness limit. This is the Serai analog of the SafeDollar accounting desync: the protocol's internal accounting (charged fee / declared transaction) diverges from what is actually broadcast, so the system can believe a payment was executed while the funds never move.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` first (`send.rs` lines 194-202). The fee/weight estimation then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204) and `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (line 226). Both calls pass `payments` — not `tx_outs` — so `calculate_weight_vbytes` (lines 62-127) builds a template `Transaction` whose outputs are only the payments and optional change; the OP_RETURN output's ~11-91 serialized bytes (8-byte value + compactsize length + script with up to 80-byte push) are never counted. Consequences:

- `needed_fee = fee_per_vbyte * vbytes` under-pays by `fee_per_vbyte * (op_return_vsize)` (lines 206, 227).
- The `TooLowFee` check compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the underestimated `vbytes` (line 211). The actual transaction can have a fee rate below 1 sat/vB (Bitcoin Core's default minimum relay feerate) even though the check passed, so the signed transaction is rejected by relay.
- The `TooLargeTransaction` check uses the underestimated `weight` (line 241), so a transaction that actually exceeds `MAX_STANDARD_TX_WEIGHT` can be signed.
- The change amount is computed as `input_sat - payment_sat - fee_with_change` (line 228); since `fee_with_change` ignores the OP_RETURN size, the *effective* fee rate is diluted, not the change — the signature commits to a TX that underpays for its true size.

Because `TransactionSignMachine::sign` sighashes the real `self.tx.tx` (which does contain the OP_RETURN output, line 373-390), the signatures are valid — the produced transaction is consensus-valid but non-standard / under-priced for relay.

`data` reaching `SignableTransaction::new` is externally influenced: in Serai, OP_RETURN data on Bitcoin burns carries the `InInstruction`/`Shorthand` payload derived from user-supplied deposit data (`get_outputs` populates `output.data` via `extract_serai_data` for `OutputType::External` in `processor/src/networks/bitcoin.rs` lines 697-733). An unprivileged depositor can therefore set `data` near the 80-byte maximum, maximizing the estimation gap.

### Impact Explanation
When `data` is present, the multisig signs and the processor broadcasts a transaction whose real fee rate is below what was requested — and potentially below the network's minimum relay feerate or above the standardness weight cap. The transaction will not propagate or confirm, yet the coordinator has already consumed the inputs' preprocesses and will record the payment/burn as signed and broadcast. This creates a ledger-level desync analogous to SafeDollar's: the system's accounting (outputs spent, payment emitted, eventuality outstanding) no longer matches the actual spendable/broadcastable state, requiring manual recovery of a stuck transaction and leaving a window where funds are neither moved nor cleanly reclaimable while the shares/preprocesses are burned.

### Likelihood Explanation
Any user attaching near-maximal `data` (up to 80 bytes, ~90 vbytes of unaccounted size) to an external Bitcoin deposit triggering an OP_RETURN-bearing outbound transaction triggers the gap deterministically — no privileged position required. Whether the signed TX actually falls below min-relay depends on `fee_per_vbyte` being near the floor (e.g., 1 sat/vB: 90 extra vbytes is enough to push the effective rate under 1 sat/vB for typical input counts); at higher fee rates the transaction still confirms but pays a lower effective rate than intended and silently inflates the measured `fee()` versus `needed_fee()`. The weight-check bypass is deterministic for any near-limit transaction carrying data.

### Recommendation
Pass the fully populated `tx_outs` (including the OP_RETURN output) into `calculate_weight_vbytes` instead of `payments` in both call sites (`send.rs` lines 204 and 226), or add the OP_RETURN output's serialized size/weight to the computed weight and vbytes before deriving `needed_fee`, `fee_with_change`, and the `MAX_STANDARD_TX_WEIGHT` / `TooLowFee` checks. After constructing the final `Transaction`, recompute `tx.weight()`/`vsize()` on the real object and assert `fee()` corresponds to at least `fee_per_vbyte` and the minimum relay feerate over the *actual* vsize.

### Proof of Concept
```rust
// From networks/bitcoin/src/wallet/send.rs context
// Given: one input ReceivedOutput of 100_000 sats, one payment of 50_000 sats,
// change address Some(change), data = Some(vec![0u8; 80]), fee_per_vbyte = 1.

let data = vec![0u8; 80];
let stx = SignableTransaction::new(
    vec![input], &[(payment_script, 50_000)], Some(change_script), Some(data), 1,
).unwrap();

// needed_fee is computed from a template WITHOUT the OP_RETURN output.
// The real transaction includes a ~91-byte OP_RETURN output.
let real_vbytes = stx.transaction().vsize() as u64;
assert!(real_vbytes > stx.needed_fee()); // needed_fee == estimated vbytes at 1 sat/vB

// Effective fee rate of the signed TX:
let actual_fee = stx.fee();            // equals needed_fee (change absorbed nothing extra)
let effective_rate = actual_fee as f64 / real_vbytes as f64;
assert!(effective_rate < 1.0);         // below DEFAULT_MIN_RELAY_TX_FEE => won't relay
// The TooLowFee check (send.rs:211) passed because it divided by the underestimated vbytes.
```
Signatures produced by `TransactionSignMachine::sign` remain valid, so the node broadcasts a syntactically valid, consensus-valid transaction that peers refuse to relay — while Serai's scanner/coordinator has already consumed the inputs and emitted the payment event.