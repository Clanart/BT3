### Title
`SignableTransaction::new` calculates the fee for a transaction without the OP_RETURN data output, underestimating the actual fee — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to M-23 (fee computed under the wrong assumptions about the actual transfer), `SignableTransaction::new` sizes and prices the transaction using only `payments` and `change`, while an OP_RETURN output carrying up to 80 bytes of caller-supplied data is appended to the real transaction but never included in the weight/vbyte calculation. The signed transaction is therefore larger than the one the fee was computed for, so it pays a lower effective fee rate than requested — potentially below the minimum relay fee.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` before the fee is computed:

- `tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })` at lines 194–202.
- The weight estimate then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204, and again `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at line 226.

`calculate_weight_vbytes` (lines 62–127) builds a template `Transaction` whose `output` vector contains only `payments` plus the optional `change` output. The `data` argument — and the OP_RETURN `TxOut` it produces — is never passed in. An OP_RETURN output with an 80-byte payload adds roughly 90 serialized bytes (~360 weight units, ~90 vbytes) to the final transaction, none of which is priced into `needed_fee`.

The minimum-relay-fee check at lines 206–213 (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) is evaluated against this underestimated `vbytes`, so it can pass even when the *actual* transaction's effective fee rate is below the relay minimum. Similarly, the change calculation at lines 224–234 subtracts `fee_with_change` that also omits the data output, so the change output receives slightly more than it should and the tx still underpays relative to its true size.

### Impact Explanation
Any `SignableTransaction` built with a non-`None` `data` argument pays less than `fee_per_vbyte` for its real size — up to ~90 vbytes unpriced. At borderline fee rates (the common case near the minimum), the resulting transaction's effective feerate falls below `DEFAULT_MIN_RELAY_TX_FEE` and is rejected by relay/mempool policy, or is simply never mined. The transaction is already committed to via `txid()`/eventuality tracking, so the payments it funds are unspendable until a replacement is constructed — funds reported as sent that cannot confirm. Like the original report, the cost was computed for a different operation than the one actually executed.

### Likelihood Explanation
The bug triggers deterministically whenever `data` is `Some`: the OP_RETURN output is unconditionally omitted from both weight calculations. Whether it crosses the relay minimum depends on `fee_per_vbyte` and how close the underestimated fee is to the floor, but the fee is *always* lower than the caller requested for the transaction actually produced. Reachability is via the public `SignableTransaction::new` API's `data` parameter.

### Recommendation
Pass the OP_RETURN output (or its serialized size) into `calculate_weight_vbytes` so both the no-change and with-change weight estimates include it. Concretely, extend `calculate_weight_vbytes` to accept the full set of outputs (`payments`, `change`, and the OP_RETURN `TxOut` built from `data`), or compute the data output's weight separately and add it to `weight`/`vbytes` at lines 204 and 226.

### Proof of Concept
```rust
// networks/bitcoin/tests/wallet.rs style
// A single input, one payment, and an 80-byte OP_RETURN payload.
let data = vec![0u8; 80];
let st = SignableTransaction::new(inputs, &payments, Some(change), Some(data), 1).unwrap();

// The template used for fee estimation has 2 outputs (payment + change).
// The real transaction has 3 outputs (payment + OP_RETURN + change).
assert_eq!(st.transaction().output.len(), 3);

// needed_fee was computed as 1 sat/vbyte * vbytes(tx without OP_RETURN).
// The actual signed tx is ~90 vbytes larger, so st.fee() / actual_vbytes < 1 sat/vbyte,
// which is below DEFAULT_MIN_RELAY_TX_FEE when actual_vbytes pushes fee/kvb under 1000 sats.
let actual_weight = {
    let mut tx = st.transaction().clone();
    for input in &mut tx.input { input.witness = Witness::from_slice(&[vec![0; 64]]); }
    tx.weight()
};
let actual_vbytes = bitcoin::policy::get_virtual_tx_size(
    i64::try_from(actual_weight.to_wu()).unwrap(), 0) as u64;
// st.needed_fee() was priced for (actual_vbytes - ~90); the real feerate
// st.fee() / actual_vbytes is below the requested rate and can drop below the relay minimum.
```
The relevant omission is at `send.rs:194-204` (data output added but not priced) and `send.rs:225-227` (with-change estimate repeats the omission).