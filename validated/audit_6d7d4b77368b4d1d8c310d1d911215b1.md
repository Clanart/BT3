### Title
Fee/weight calculation ignores the OP_RETURN data output, producing an underestimated `needed_fee` and bypassing the max-weight check - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes the transaction weight/vbytes (and therefore `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check) from `payments` before the caller-supplied `data` OP_RETURN output is accounted for. The data output is pushed into `tx_outs` at line 195, but `calculate_weight_vbytes` is invoked at lines 204 and 226 with `payments` only — a parallel to `totalValues` being computed before fees are applied and then used for a downstream validity check. The signed transaction is therefore larger and heavier than the values used for every fee/size decision, and the leftover intended as `needed_fee` can fall below the requested feerate or even below the relay minimum.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`):

1. `tx_outs` is built from `payments`, then the OP_RETURN output is appended when `data` is `Some` (lines 193-202, up to 80 bytes plus output overhead).
2. `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` is then called at line 204 — it reconstructs the weight using only `payments`, never `tx_outs`, so the data output contributes zero weight.
3. `needed_fee = fee_per_vbyte * vbytes` and the `TooLowFee` minimum-relay check (lines 206-213) use this undercounted `vbytes`.
4. The change calculation at lines 224-235 repeats the same omission (`calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`), so `change_amount` is credited with a fee that doesn't reflect the data output's size.
5. The `TooLargeTransaction` standardness check at line 241 uses `weight` that likewise excludes the data output and, when the change output is dropped as dust, also uses the pre-change `weight`.

Like the reported bug — where `totalValues` is populated before fees reduce basket values and the stale total then drives the deviation check — here a size/fee aggregate is computed before an output is added and the stale aggregate drives funding sufficiency, feerate compliance, and standardness checks. The caller supplies `data` (untrusted bytes, bounded at 80 via `TooMuchData`), so the discrepancy is directly attacker/caller-controlled.

### Impact Explanation
- **Underpaid fee / non-relayable transaction**: the actual transaction's vsize exceeds `vbytes`, so the effective feerate is strictly below `fee_per_vbyte`. When `change` is absent or dust, the leftover is swept as fee anyway; the `TooLowFee` check passed against the smaller estimate, so the resulting transaction can fall below `DEFAULT_MIN_RELAY_TX_FEE` for its true size and be rejected by the network — funds committed to a signed transaction that cannot be broadcast/spent.
- **Standardness check bypass**: a transaction whose true weight exceeds `MAX_STANDARD_TX_WEIGHT` (400,000 WU) can pass line 241 because up to ~360+ weight units (80-byte OP_RETURN output plus serialization overhead) are uncounted. Signing then produces a transaction nodes refuse to relay.

### Likelihood Explanation
`SignableTransaction::new` is a public constructor and `data` is arbitrary caller-controlled bytes accepted up to 80 bytes. Any invocation with `data: Some(_)` deterministically triggers the miscalculation — no adversarial coordination required. The impact is bounded (one missed output's worth of fee/weight), but the check failure is systematic, not probabilistic.

### Recommendation
Compute weight/vbytes from the actual `tx_outs` being built (or pass a payments slice augmented with the data output), and re-evaluate `needed_fee`, `TooLowFee`, the change amount, and `TooLargeTransaction` against the final output set — i.e., keep the aggregate in sync with every mutation, mirroring the report's recommendation to feed updated totals into the check.

### Proof of Concept
```rust
// networks/bitcoin conceptual PoC: inputs worth 100_000 sats, one payment, 80-byte data
let data = Some(vec![0u8; 80]); // maximal allowed payload
let tx = SignableTransaction::new(inputs, &payments, None, data.clone(), fee_per_vbyte)
  .unwrap();

// The OP_RETURN output exists in the final transaction:
assert!(tx.transaction().output.iter().any(|o| o.script_pubkey.is_op_return()));

// ...but was excluded from the vbytes used for needed_fee:
let (w_no_data, vb_no_data) = // weight computed over payments only, as at send.rs:204
let actual_vb = tx.transaction().vsize() as u64; // includes the ~90-byte data output
assert!(actual_vb > vb_no_data);
// needed_fee() == fee_per_vbyte * vb_no_data < fee_per_vbyte * actual_vb,
// so the broadcast transaction pays a lower feerate than requested;
// near the DEFAULT_MIN_RELAY_TX_FEE or MAX_STANDARD_TX_WEIGHT boundary it
// is non-standard and cannot be relayed.
```

This is the structural equivalent of the H-01 pattern in `networks/bitcoin/src/wallet/send.rs:193-241`: an aggregate computed before a mutating step, then consumed by funding/validity checks as if still accurate.