### Title
`SignableTransaction::new` computes weight/vsize and `needed_fee` without the OP_RETURN `data` output, so the signed transaction pays a lower effective fee rate than requested and reported - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts an optional `data` payload which is appended to the real transaction as an extra OP_RETURN output (`tx_outs.push(...)` at send.rs:194-202). However, both fee/weight estimations are performed by `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which reconstructs a template transaction built **only from `payments`** (and optionally `change`) — the OP_RETURN output is never included (send.rs:85-99, 204, 225-226). The analog of the fee-on-transfer bug class ("accounting done on an assumed/reported amount that differs from the actual amount moved"): the fee and size accounting is computed on a transaction that does not match the transaction actually signed and broadcast.

### Finding Description
- The final signed transaction contains `payments + optional OP_RETURN + optional change` outputs (send.rs:188-235, 245-251).
- `calculate_weight_vbytes` builds its template from `tx.input` and `payments.iter().map(...)` only; `data` is not a parameter and cannot influence `weight`/`vbytes` (send.rs:62-99).
- `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) and `fee_with_change = fee_per_vbyte * vbytes_with_change` (send.rs:227) therefore both understate the true serialized size by the entire OP_RETURN output (roughly `8 (value) + 1 (script len) + ~2 + data_len` serialized bytes, plus the compactsize output-count increment — up to ~92 vbytes for an 80-byte payload).
- Because `change` is computed as `input_sat - payment_sat - fee_with_change` (send.rs:228-230), the change output absorbs the shortfall, so `fee() == needed_fee` still holds — but the transaction is up to ~92 vbytes heavier than `needed_fee / fee_per_vbyte`, meaning the actual paid fee rate is strictly below the caller-specified `fee_per_vbyte`.
- The `MAX_STANDARD_TX_WEIGHT` check at send.rs:241 also runs against the underestimated `weight`, and the `TooLowFee` minimum-relay check at send.rs:211 validates against the underestimated vbytes.

### Impact Explanation
An unprivileged caller that causes a send carrying `data` (the in-protocol path that attaches metadata to payout transactions) produces a transaction whose real fee rate is lower than the rate the scheduler/multisig intended and lower than what `needed_fee()` reports to downstream accounting. Concretely:

- The `TooLowFee` guard can pass while the real transaction is below `DEFAULT_MIN_RELAY_TX_FEE`, producing a signed transaction that will not relay — the inputs it commits (`Prevouts::All`) are effectively stuck for that signing round and the multisig signed a transaction that cannot propagate.
- The standardness check can pass while the real transaction exceeds `MAX_STANDARD_TX_WEIGHT` (an oversized payment set padded with data).
- Effective fee rate is lower than requested, so time-sensitive payouts can sit unconfirmed, and any consumer treating `needed_fee()` as the achieved fee rate is misled — the protocol "loses" the difference between intended and delivered feerate, mirroring the reported class where the amount accounted for differs from the amount actually transferred.

### Likelihood Explanation
The bug triggers whenever `SignableTransaction::new` is invoked with `data: Some(_)` — a fully public-input-driven path (transaction data the protocol is asked to embed). No collusion or validator misbehavior is required; it is a deterministic miscalculation in the fee/weight formula. Severity is bounded because the change output absorbs the discrepancy (no direct satoshi loss), but the deliverable property (the signed TX meeting the specified fee rate and standardness bounds) is violated.

### Recommendation
Pass the fully-populated `tx_outs` (including the OP_RETURN output) — or the actual `Transaction` — into `calculate_weight_vbytes`, and recompute both the no-change and with-change estimates on the real output set. Concretely:

```rust
// send.rs — build the template over the real outputs
fn calculate_weight_vbytes(tx_ins: &[TxIn], tx_outs: &[TxOut]) -> (u64, u64) {
  let tx = Transaction {
    version: Version(2),
    lock_time: LockTime::ZERO,
    input: tx_ins.to_vec(),
    output: tx_outs.to_vec(),
  };
  let weight = tx.weight();
  ...
}
```
Call it after the OP_RETURN output is pushed into `tx_outs`, and again after the change output is pushed, so `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the transaction that is actually signed.

### Proof of Concept
In `SignableTransaction::new` (send.rs:150-256):

```rust
// Conceptual: create a tx with an 80-byte OP_RETURN payload
let st = SignableTransaction::new(inputs, &payments, None, Some(vec![0u8; 80]), fee_per_vbyte)?;
// st.needed_fee() == fee_per_vbyte * vbytes(template_without_op_return)
// st.transaction() includes the OP_RETURN output => real vsize > template vsize
// => real feerate = st.fee() / real_vsize < fee_per_vbyte
```

`calculate_weight_vbytes` at send.rs:85-93 builds outputs strictly from `payments`, while `data` is pushed to `tx_outs` at send.rs:194-202 and never reaches the estimator. With `data` near the 80-byte cap, the signed transaction is ~90 vbytes larger than the size `needed_fee` was priced on, and the standardness/relay-fee guards at send.rs:211 and send.rs:241 are evaluated against the smaller phantom transaction.