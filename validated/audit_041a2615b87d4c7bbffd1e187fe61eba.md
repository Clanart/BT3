### Title
SignableTransaction::new() omits the OP_RETURN data output from the fee/weight calculation, underpaying the intended fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The reported bug is an incorrect arithmetic formula that computes a materially smaller amount than intended (a spurious extra division collapsing the minted amount to ~0). The Serai analog is a weight/vbytes formula that omits one of the transaction's outputs, so the "needed fee" is computed for a smaller transaction than the one actually signed — the same class of bug: the formula does not account for a real term of the quantity it claims to measure.

### Finding Description
`SignableTransaction::new` builds `tx_outs` from the payments, then appends an OP_RETURN output when `data` is supplied (send.rs:194-202). However, the fee calculation calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (send.rs:204) and `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (send.rs:226), both of which construct the model transaction's `output` vector **only from `payments`** (send.rs:85-93) plus optionally the change output. The OP_RETURN output — which can carry up to 80 bytes of data plus output overhead — is present in the final `Transaction` but absent from the model used to derive `weight` and `vbytes`.

Consequently `needed_fee = fee_per_vbyte * vbytes` (send.rs:206, 227) and the minimum-relay-fee check (send.rs:211) are computed against an understated size. The transaction still balances internally (the change output simply absorbs `input_sat - payment_sat - fee_with_change`, so `fee()` equals `needed_fee`), but the fee *rate* actually paid is `needed_fee / actual_vbytes`, which is strictly less than the caller-specified `fee_per_vbyte`.

### Impact Explanation
Any caller that attaches `data` produces a transaction paying a lower fee rate than requested. With a large enough data payload (up to 80 bytes, i.e., ~90+ extra weight units in the output plus the output base), the true fee rate can fall below the network's minimum relay fee even though the check at send.rs:211 passed, producing a transaction that is signed by the FROST `TransactionMachine` but will not relay or confirm — burning a FROST signing session and stalling the spend. This is "incorrect formula producing a wrong amount," the same bug class as the USSD mint miscalculation, mapped onto the fee/change/dust math surface.

### Likelihood Explanation
The data path is a documented feature of `SignableTransaction::new` ("If data is specified, an OP_RETURN output will be added with it"), and the miscalculation triggers deterministically whenever `data.is_some()` — no adversarial condition required. The severity is bounded (underpayment is proportional to the omitted output size, not a collapse to zero), so this is Medium rather than High.

### Recommendation
Include the data output in the model transaction inside `calculate_weight_vbytes`, or pass the fully-built `tx_outs`/`data` into it so the measured weight covers every output that will appear in the signed transaction. Recompute `needed_fee` from that complete size in both the no-change and change branches.

### Proof of Concept
```rust
// networks/bitcoin: construct payments and ~80-byte data so the OP_RETURN
// output is added to tx_outs (send.rs:194-202).
let tx = SignableTransaction::new(inputs, &payments, Some(change), Some(vec![0xaa; 80]), 10).unwrap();

// needed_fee was derived from calculate_weight_vbytes(inputs, payments, change),
// whose model tx (send.rs:85-98) contains no OP_RETURN output.
let model_vbytes = tx.needed_fee() / 10;

// The real transaction includes the OP_RETURN output:
assert!(tx.transaction().output.iter().any(|o| o.script_pubkey.is_op_return()));
// real_vsize > model_vbytes, so the effective fee rate is < 10 sat/vbyte
// even though the caller asked for 10 and the TooLowFee check passed.
```

The fix is a one-line change in what `calculate_weight_vbytes` is asked to model; today the OP_RETURN bytes are signed but never weighed.