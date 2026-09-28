### Title
`SignableTransaction::new` omits the OP_RETURN output from fee/weight calculation, producing under-priced transactions that can leave funds unspendable — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the reported class — protocol value not properly accounted for after an operation — `SignableTransaction::new` fails to account for the `data` (OP_RETURN) output when computing transaction weight/vbytes and hence the fee. The result is a transaction that pays a lower fee rate than the caller requested; if the true fee rate falls below the relay minimum, the transaction will never propagate, leaving the spent inputs' funds effectively locked.

### Finding Description
`SignableTransaction::new` builds `tx_outs` including the OP_RETURN output when `data` is provided (lines 194-202), but then computes weight/vbytes via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204, which reconstructs a transaction containing *only* `payments` — the OP_RETURN output is never included. The same omission occurs in the change path at lines 224-234, where `fee_with_change` is computed from `vbytes_with_change` that also excludes the OP_RETURN output (up to 83 bytes, since `data` may be up to 80 bytes).

Consequences:
1. `needed_fee = fee_per_vbyte * vbytes` undercharges: the actual on-chain transaction is larger than the estimate.
2. The minimum-relay-fee check at line 211 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes` using the same underestimated `vbytes`, so a transaction that would actually be below the minimum relay fee passes validation.
3. With a change output, change is computed as `input_sat - payment_sat - fee_with_change`, so the "excess" (the unpaid portion of the real fee) is diverted to the change output rather than miners — the transaction broadcasts at a lower feerate than specified, and if the real feerate is below the relay minimum the TX is dropped by the network.

Because inputs use `Sequence::MAX` (no RBF signaling) and the inputs are already consumed, the funds are stuck until the (underpriced) transaction is somehow mined or the wallet is manually recovered.

### Impact Explanation
An unprivileged caller supplying `data` to `SignableTransaction::new` produces a signed transaction whose fee rate is lower than requested and potentially below `DEFAULT_MIN_RELAY_TX_FEE`. Unlike the original report (tokens stranded in-contract), here the miscounted bytes cause a signed transaction that the network will not relay/confirm, leaving the underlying UTXOs unusable — a direct "funds received/committed that are not spendable" impact.

### Likelihood Explanation
Triggering requires calling `SignableTransaction::new` with a non-`None` `data` parameter — a normal, documented code path in the public wallet API. The error scales with `data.len()` (up to 80 bytes ≈ 80+ vbytes), so at low `fee_per_vbyte` values (e.g., 1 sat/vB near the minimum), even modest data pushes the true feerate below the relay floor. No attacker cooperation or unusual state is needed.

### Recommendation
Include the OP_RETURN output when computing weight/vbytes — e.g., pass the fully-built `tx_outs` (or a payments+data list) into `calculate_weight_vbytes` instead of only `payments`, in both the no-change and change paths. Re-run the minimum-fee check against the corrected vsize.

### Proof of Concept
```rust
// Conceptual: any call with data != None underestimates the fee.
let tx = SignableTransaction::new(
    vec![input],
    &payments,
    Some(change_script),
    Some(vec![0u8; 80]), // OP_RETURN output of ~83 bytes
    1,                   // 1 sat/vbyte, near relay minimum
).unwrap();
// tx.needed_fee() was computed over vbytes excluding the ~83-byte OP_RETURN output,
// so tx.fee() < true_vsize * 1 sat/vB and potentially below
// DEFAULT_MIN_RELAY_TX_FEE * true_vsize / 1000 -> the TX is not relayed.
```

Key code: OP_RETURN added to `tx_outs` at `networks/bitcoin/src/wallet/send.rs:194-202`; weight calculated from `payments` only at `send.rs:204` and `send.rs:225-226`; min-fee check at `send.rs:211`; change value derived from the underestimated `fee_with_change` at `send.rs:228-230`.