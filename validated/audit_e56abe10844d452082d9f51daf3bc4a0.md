### Title
SignableTransaction computes weight/fee over a stale output set, omitting the OP_RETURN data output - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes an `OP_RETURN` output carrying `data` into `tx_outs`, but then calls `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which reconstructs the transaction using only `payments` (and optionally `change`). The `data` output is never included in the weight/vbytes calculation. This is the same bug class as the Hyper report: a derived value (the fee/weight, analogous to pool reserves) is computed under one parameter set and then reused after the object's shape has changed, so the committed "virtual" accounting no longer matches the actual transaction.

### Finding Description
In `SignableTransaction::new`:

1. `tx_outs` is built from `payments`, then an `OP_RETURN` output of up to 80 bytes is appended (lines 193-202).
2. `calculate_weight_vbytes` is called with `payments` — not `tx_outs` — at lines 204 and 225. It builds a mock `Transaction` whose outputs are only the payments plus optional change (lines 68-99), so the OP_RETURN output's weight (~83+ bytes: amount + script length + up to 82 bytes of script) is excluded.
3. Consequently `needed_fee = fee_per_vbyte * vbytes` (line 206) and the change-amount computation `input_sat - payment_sat - fee_with_change` (line 228) both use vbytes that understate the final transaction's real size.
4. The minimum-relay-fee check at line 211 and the `MAX_STANDARD_TX_WEIGHT` check at line 241 are likewise evaluated against the stale weight, so a transaction that is actually under the relay minimum or over the standardness limit passes validation.
5. The final `Transaction` stored in `SignableTransaction` includes the OP_RETURN output, and `fee()` reports `sum(inputs) - sum(outputs)`, which equals `needed_fee` — an amount smaller than `fee_per_vbyte` times the real vsize.

Because the change output absorbs the remainder (`input_sat - payment_sat - fee_with_change`), the effective fee rate of the broadcast transaction is strictly lower than the `fee_per_vbyte` the caller requested and was validated against.

### Impact Explanation
The signed transaction pays a lower fee rate than intended. If `data` is present and pushes the real fee rate below `DEFAULT_MIN_RELAY_TX_FEE` (the check at line 211 was computed on the smaller, OP_RETURN-free vbytes), the transaction will not relay or confirm, leaving multisig inputs locked in an unconfirmable spend that every honest signer has already committed a nonce/signature to. Additionally, `weight` excludes the OP_RETURN output, so transactions near `MAX_STANDARD_TX_WEIGHT` can be constructed that exceed the standardness limit. This produces funds committed to a spend that is not reliably spendable — a concrete availability loss for the Bitcoin wallet.

### Likelihood Explanation
Any `SignableTransaction::new` call with `data: Some(...)` triggers the miscalculation deterministically. In the Serai protocol `data` is used for forwarded outputs, so this path is exercised by user-initiated forwards containing memo/refund payloads. The magnitude of underpayment grows with the data length (up to ~83 vbytes unaccounted), making the under-relay-fee outcome plausible whenever the requested `fee_per_vbyte` is at or near the minimum.

### Recommendation
Build the mock transaction inside `calculate_weight_vbytes` from the actual output list (payments + OP_RETURN + optional change), or equivalently pass the data output through as an additional output so all weight-affecting components are counted before `needed_fee`, the minimum-fee check, the change computation, and the `MAX_STANDARD_TX_WEIGHT` check are evaluated. Conceptually: recompute all derived values after every parameter/output mutation, mirroring the Hyper recommendation of realigning reserves after `changeParameters`.

### Proof of Concept
In `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150):

```rust
// data output pushed into the real tx's output set
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) });
}

// fee/weight computed ONLY over payments — `data` output is omitted
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` (lines 62-127) constructs `tx.output` solely from `payments` and `change`; there is no parameter or call site that injects the OP_RETURN output. With `data` of 80 bytes, the real transaction is ~83 bytes larger than measured, so `fee()/tx.vsize() < fee_per_vbyte` and the relay-minimum check at line 211 is satisfied against a smaller transaction than the one signed. A unit test: construct a `SignableTransaction` with `data: Some(vec![0; 80])`, compare `needed_fee` against `fee_per_vbyte * real_signed_tx.vsize()` — they differ by the OP_RETURN output's weight.