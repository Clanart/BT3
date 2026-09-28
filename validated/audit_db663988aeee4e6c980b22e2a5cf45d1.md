### Title
OP_RETURN data output excluded from weight/fee calculation, causing fee miscounting and oversized transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes an OP_RETURN `data` output into `tx_outs`, but computes the transaction's weight, virtual size, `needed_fee`, change amount, and the `MAX_STANDARD_TX_WEIGHT` check using `calculate_weight_vbytes`, which only accounts for the payment and change outputs. The data output is invisible to all fee/size math.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output (up to 80 bytes of caller-supplied data) is appended to `tx_outs` at lines 194–202. Immediately after, `(mut weight, vbytes)` is computed via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204 — a helper that reconstructs a transaction containing only the payment outputs (lines 85–94). `data` is never passed to it. The same omission applies to the change path at lines 224–235, where `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` again excludes the data output.

Consequences in the produced transaction:
- `needed_fee = fee_per_vbyte * vbytes` underestimates the required fee by `fee_per_vbyte * (size of the OP_RETURN output)` — roughly 12 + data_len vbytes (~91 vbytes for 80 bytes of data).
- The change output is computed as `input_sat - payment_sat - fee_with_change`, where `fee_with_change` is also underestimated. The change is therefore too large, and the actual fee paid (`sum(inputs) - sum(outputs)`, per `fee()`) exceeds `needed_fee()`. Callers relying on `needed_fee()` (e.g., the processor amortizes exactly this fee across payments) will misaccount funds — the excess is burned to miners.
- The minimum-relay-fee check at line 211 and the `MAX_STANDARD_TX_WEIGHT` check at line 241 both use the underestimated `weight`/`vbytes`. A transaction whose true weight exceeds the standardness limit (or whose true feerate falls below minimum relay) can be constructed and signed as "valid", producing an unbroadcastable transaction that has already consumed/signing-committed the inputs.

This mirrors the Formation.Fi class: the code "underestimates the impact of [a component] on the total" — the signed transaction's real weight/fee differs from the accounted weight/fee, so `needed_fee()` does not reflect what is actually paid.

### Impact Explanation
Any caller of `SignableTransaction::new` that supplies `data` receives a transaction whose actual fee exceeds the reported `needed_fee()` and whose change output is inflated by the missing output's cost. The difference is paid to miners — a direct, quantifiable loss of funds per transaction, scaling linearly with `fee_per_vbyte`. In the worst case, a transaction near the weight limit or fee floor passes all checks yet is non-standard/under-fee once the data output is included, causing the signed transaction to be rejected and the plan's inputs to be wasted or stuck. No privileged position is required; `data` is a public parameter.

### Likelihood Explanation
The flaw triggers deterministically whenever `data` is `Some(_)` — it is not edge-case-dependent. The only mitigating factor is that the current in-tree processor caller passes `None` for data, so exploitation requires an integrator (or future code path) that attaches OP_RETURN data while using this API — which is exactly the use case `data` exists for, and OP_RETURN burns are how Serai deposits encode `Shorthand::transfer` instructions elsewhere in the codebase.

### Recommendation
Include the data output in `calculate_weight_vbytes`: pass the constructed `tx_outs` (or `data` length) into the helper so the weight/vbyte estimate reflects the final transaction, e.g., build the output list as `payments + optional OP_RETURN + optional change`. Alternatively, compute weight directly from the final `tx` before signing. Also re-derive `needed_fee` and the dust/change and standardness checks from that complete weight.

### Proof of Concept
```rust
// In networks/bitcoin, construct a tx with 80 bytes of OP_RETURN data
let tx = SignableTransaction::new(
  vec![input],                 // a ReceivedOutput
  &[(payment_script, 1000)],   // a payment
  Some(change_script),         // change address
  Some(vec![0xAA; 80]),        // OP_RETURN data
  fee_per_vbyte,
).unwrap();

// The returned transaction contains 3 outputs (payment, OP_RETURN, change)
assert_eq!(tx.tx.output.len(), 3);

// needed_fee was computed without the ~91-vbyte OP_RETURN output:
// tx.needed_fee() == fee_per_vbyte * vbytes_without_data
// Actual fee paid: inputs - outputs, which is larger by
//   fee_per_vbyte * (~91 vbytes) — silently overpaid to miners.
// Additionally, tx.fee() != tx.needed_fee().
assert!(tx.fee() > tx.needed_fee());
```