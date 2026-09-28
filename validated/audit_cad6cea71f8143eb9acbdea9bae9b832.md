### Title
OP_RETURN data output is excluded from the transaction weight used for fee and minimum-fee checks, producing a signed transaction that underpays its fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` validates `needed_fee >= DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` and charges `fee_per_vbyte * vbytes`, but both weight calculations are performed over only the `payments` outputs. The OP_RETURN output carrying user-specified `data` (up to 80 bytes) is pushed into `tx_outs` *before* the weight is measured, yet is never included in the measured transaction. The invariant "fee checked >= minimum, then transaction is emitted" is broken because the final transaction is larger than the one the fee was validated against — a lower-bound (fee rate) check that is not re-evaluated after the output set is adjusted.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

- Lines 165-169 check each payment amount is `>= DUST`.
- Lines 194-202 push an OP_RETURN `TxOut` into `tx_outs` when `data` is present.
- Line 204 calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — note it passes `payments`, the `&[(ScriptBuf, u64)]` slice, not `tx_outs`. `calculate_weight_vbytes` (lines 62-99) builds a mock transaction whose outputs are exactly `payments` plus optional `change`; the data output is absent.
- Line 211 rejects when `needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`, using the under-measured `vbytes`.
- Lines 224-235 repeat the same omission when the change output is contemplated: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` again excludes the data output, and `fee_with_change` is computed on the same too-small vbytes.

The result: the returned `SignableTransaction`'s `tx` contains an extra output (the OP_RETURN, up to ~90+ bytes: 8-byte value + script with up to 80 bytes of pushed data) that was never priced. `fee()` = `sum(inputs) - sum(outputs)` is unchanged in absolute terms, so the *effective* fee rate `fee / actual_vbytes` is strictly less than the requested `fee_per_vbyte`, and can fall below `DEFAULT_MIN_RELAY_TX_FEE` even though the `TooLowFee` check passed.

This is the same class as the reference finding: an invariant (`lockedValues >= minAcceptableQuoteValue`; here `fee >= min_relay` and `fee == fee_per_vbyte * vbytes`) is validated on one value, the value/output set is then mutated (openedPrice adjustment; OP_RETURN appended), and the mutated result is committed without re-checking.

### Impact Explanation
A transaction built with a `data` payload is signed (each input via `TransactionSignMachine::sign` → `taproot_key_spend_signature_hash`) committing to a fee that underpays relative to the negotiated rate. When `fee_per_vbyte` is near the relay minimum, the produced transaction is below `DEFAULT_MIN_RELAY_TX_FEE` for its true vsize and will be rejected by the mempool — a signed, broadcast-failing transaction. Even at higher rates, callers relying on `needed_fee()`/`fee()` contract get a systematically lower effective fee rate than requested, degrading confirmation reliability for any data-bearing send. Because the shortfall is bounded by the ~90-byte OP_RETURN, this is Medium rather than High.

### Likelihood Explanation
`data: Option<Vec<u8>>` is an untrusted, caller-supplied field of `SignableTransaction::new` — the payment/data bytes are public inputs any party able to initiate a Bitcoin send with a data payload can set (the constructor is the sole entry point for producing spendable signed transactions in this crate). The bug is deterministic: whenever `data.is_some()`, every fee and weight check is computed on a transaction strictly smaller than the one signed. No race or privileged position is required; supplying a non-empty `data` vector suffices.

### Recommendation
Include the data output in the weight/fee calculation, e.g. compute `tx_outs` (payments + OP_RETURN) first and pass the complete output list into `calculate_weight_vbytes` for both the no-change and with-change measurements, or account for the OP_RETURN size explicitly. Alternatively, re-validate `needed_fee` against the final transaction's `tx.weight()`/`vsize()` after all outputs (data, change) are assembled, erroring with `TooLowFee` if the minimum is no longer met.

### Proof of Concept
Conceptual test (against `bitcoin-serai` with a funded scanner output):

```rust
// inputs: one ReceivedOutput of value V
// payments: [(p2tr_script_buf(key), 1000)]
// data: Some(vec![0u8; 80])        // 80-byte OP_RETURN payload
// fee_per_vbyte: DEFAULT_MIN_RELAY_TX_FEE/1000-equivalent minimal rate

let tx = SignableTransaction::new(inputs, &payments, None, Some(vec![0; 80]), fee_per_vbyte)
  .unwrap(); // passes TooLowFee because vbytes excludes the OP_RETURN output

// The signed transaction's real vsize includes the data output
let actual_vsize = tx.transaction().vsize() as u64;
let actual_fee = tx.fee();

// effective fee rate < fee_per_vbyte, and possibly < min relay
assert!(actual_fee < fee_per_vbyte * actual_vsize);
// With a minimal fee_per_vbyte, actual_fee < DEFAULT_MIN_RELAY_TX_FEE * actual_vsize / 1000,
// i.e. the tx is unrelayable despite passing the TooLowFee check.
```

Root cause is `calculate_weight_vbytes` being fed `payments` rather than the full `tx_outs` (which already contains the OP_RETURN at the time of measurement), at `networks/bitcoin/src/wallet/send.rs:194-226`. Note: the sole observed in-repo caller (`processor/src/networks/bitcoin.rs`) currently passes `data: None`, so exploitability depends on any plan path that forwards user-supplied data to Bitcoin sends; if none exists, the bug is latent in the library API.