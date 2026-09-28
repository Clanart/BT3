### Title

Transaction weight/fee accounting ignores the OP_RETURN data output, producing an underpriced and potentially non-standard transaction — (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary

The audit-issue bug class is an aggregate/global accounting variable that is not updated when an element is added or removed. In `SignableTransaction::new`, when a `data` payload is supplied, an `OP_RETURN` output is pushed onto `tx_outs`, but both `calculate_weight_vbytes` calls only serialize `payments` (and optionally `change`) into the measurement transaction — the `OP_RETURN` output is never included in `weight`/`vbytes`. The resulting `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check are computed on a stale aggregate.

### Finding Description

`SignableTransaction::new` builds the final output list `tx_outs` including an `OP_RETURN` output carrying up to 80 bytes of attacker-influenced data (`send.rs:194-202`). However, fee and size accounting call `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` and `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (`send.rs:204`, `send.rs:225-226`), whose internal measurement transaction is constructed solely from `payments` plus an optional change output (`send.rs:85-99`). The `OP_RETURN` output — up to ~90 bytes, i.e. ~330 weight units / ~83 vbytes — is omitted from:

1. `needed_fee` = `fee_per_vbyte * vbytes` (`send.rs:206`, `send.rs:232`): the transaction pays the requested rate for a smaller transaction than the one actually signed and broadcast.
2. The standardness check `weight > MAX_STANDARD_TX_WEIGHT` (`send.rs:241`): a transaction at the boundary can pass this check while its real weight exceeds the 400,000 WU standardness limit, making it non-relayable.

The change computation is internally consistent (change = `input_sat - payment_sat - fee_with_change`, `send.rs:228-233`), so the transaction still balances — the defect is that the feerate target and the standardness bound are enforced against the wrong aggregate.

### Impact Explanation

An unprivileged party controls the `data` payload end-to-end: on Bitcoin, arbitrary `OP_RETURN`-embedded `InInstruction` data in an external transaction is what drives the processor to construct refund/forward/branch transactions via `SignableTransaction::new`. By causing transactions carrying data to be produced, the attacker ensures every such transaction is signed with a fee lower than the intended `fee_per_vbyte` (underpaying by up to ~83 vbytes × rate), degrading confirmation reliability, and in boundary cases produces a transaction exceeding `MAX_STANDARD_TX_WEIGHT` that standard nodes will not relay — i.e., funds are moved into a spend that cannot propagate, functionally equivalent to funds reported handled that are not spendable until a corrected transaction is produced.

### Likelihood Explanation

Any external Bitcoin sender can attach data that results in data-carrying outputs being constructed, so the mispriced fee is reached through normal operation whenever `data` is `Some`. The standardness-limit bypass additionally requires the transaction to already sit near 400,000 WU (achievable with large input/output counts bounded by `MAX_OUTPUTS`), making the non-relayable outcome lower-probability, but the silent fee underpayment occurs on every data-bearing transaction. Severity is bounded by the ~83-vbyte magnitude, consistent with Medium.

### Recommendation

Include the `OP_RETURN` output in the weight/vbytes measurement. The cleanest fix is to build the measurement `Transaction` in `calculate_weight_vbytes` from the same output list that will be committed to (pass `tx_outs`, or an additional `data` parameter, into `calculate_weight_vbytes`), so `weight`, `vbytes`, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the actual signed transaction.

### Proof of Concept

```rust
// networks/bitcoin context; inputs/payments/change/data as in SignableTransaction::new
let data = vec![0u8; 80];
let tx = SignableTransaction::new(inputs, &payments, change, Some(data), fee_per_vbyte).unwrap();

// The real transaction contains the OP_RETURN output...
assert!(tx.transaction().output.iter().any(|o| o.script_pubkey.is_op_return()));

// ...but needed_fee was computed as if it did not exist.
// Recompute the vbytes the code used (payments only) vs the actual vbytes:
let (_, billed_vbytes) = SignableTransaction::calculate_weight_vbytes(n_inputs, &payments, change.as_ref());
let actual_vbytes = u64::try_from(tx.transaction().vsize()).unwrap();
assert!(actual_vbytes > billed_vbytes);              // OP_RETURN weight excluded
assert_eq!(tx.needed_fee(), fee_per_vbyte * billed_vbytes); // stale aggregate billed
// Effective feerate = needed_fee / actual_vbytes < fee_per_vbyte.
// If actual weight > MAX_STANDARD_TX_WEIGHT while billed weight <= it,
// the check at send.rs:241 passes for a non-standard transaction.
```

Root cause: the measurement transaction in `calculate_weight_vbytes` (`networks/bitcoin/src/wallet/send.rs:85-99`) is built only from `payments`/`change`, while the committed `tx_outs` (`send.rs:188-202`, `send.rs:230`) additionally include the `OP_RETURN` output — the aggregate was never updated when the output was added.