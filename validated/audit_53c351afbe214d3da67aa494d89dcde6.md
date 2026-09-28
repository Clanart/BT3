### Title
`SignableTransaction::new` omits the OP_RETURN data output from the weight/vbytes used to compute the fee, so the added output is not accounted for — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The external report's bug class — an operation adds value/state but fails to update the corresponding accounting total, so downstream math is computed on a stale figure — maps directly onto `SignableTransaction::new`. An OP_RETURN `TxOut` is pushed onto `tx_outs`, but `calculate_weight_vbytes` is invoked with only `payments` and the optional `change`; the data output is never included in the transaction used to derive `weight`/`vbytes`. The result is a `needed_fee` computed against a smaller transaction than the one actually produced — the exact "updated the outputs, not the total" pattern, with the fee total playing the role of the stale accounting variable.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

1. The OP_RETURN output is appended to `tx_outs` before any weight calculation (lines 194–202).
2. `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204) builds a scratch `Transaction` whose `output` list is constructed **only** from `payments` (lines 85–93), plus optionally `change` (lines 95–99). The OP_RETURN output — up to 80 bytes of data plus output overhead — is absent.
3. `needed_fee = fee_per_vbyte * vbytes` (line 206) therefore prices a transaction ~84–95 weight units smaller than the real one. The `TooLowFee` check against `DEFAULT_MIN_RELAY_TX_FEE` (line 211) is evaluated against this same underestimated `vbytes`, so a transaction whose real fee rate falls below the relay minimum is accepted.
4. When change is present, `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (lines 225–227) repeats the same omission, so `fee_with_change` is also undercomputed and the change output absorbs the difference (line 228–233), silently lowering the effective fee rate below what the caller requested via `fee_per_vbyte`.

The `needed_fee`/`weight`/`tx_outs` accounting is internally inconsistent: an output exists in `tx_outs` that was never added to the weight total it is charged against.

### Impact Explanation
- Without change: the produced transaction pays `needed_fee`, which may be below the actual minimum relay fee for its true vsize — the node rejects it and the (threshold-signed) spend never relays.
- With change: the transaction confirms at a lower sat/vbyte than requested; during fee spikes this can delay or stall confirmation of funds movement.
- `needed_fee()` and `fee()` report divergent accounting: callers amortizing `needed_fee` over payments (as done in `processor/src/networks/mod.rs`) underbudget every data-carrying transaction.

### Likelihood Explanation
Triggered whenever `data` is `Some` — i.e., any Bitcoin transaction embedding OP_RETURN data — which is a routine code path, not an edge case. The discrepancy is deterministic: every such transaction is underpriced by `fee_per_vbyte * (vbytes of the OP_RETURN output)`. Whether it causes an actual relay failure depends on how close `fee_per_vbyte` is to the relay minimum, so the practical impact is an underpaid fee on every occurrence rather than a guaranteed rejection.

### Recommendation
Include the OP_RETURN output (and generally, every output actually serialized into `tx_outs`) in the scratch transaction used by `calculate_weight_vbytes` — e.g., pass the full `tx_outs` list or add the data output to `payments` for weighing purposes — for both the change and no-change fee calculations.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// SignableTransaction::new(..., data: Some(vec![0u8; 80]), fee_per_vbyte: 1)

// tx_outs gains an OP_RETURN TxOut (~91 serialized bytes) at line 195,
// but calculate_weight_vbytes is called with `payments` only:
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
// `vbytes` excludes the OP_RETURN output => needed_fee = vbytes * 1
// is ~91 sats short of the fee for the real transaction's size.
// With `change`, `fee_with_change` is undercomputed identically, so the
// change output (line 230) absorbs the difference and the transaction's
// true feerate is below `fee_per_vbyte`.
```