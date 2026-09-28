### Title
Fee and weight calculation omit the OP_RETURN data output, producing an under-fee/oversized transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends a caller-supplied `data` payload as a zero-value `OP_RETURN` output to `tx_outs`, but both invocations of `calculate_weight_vbytes` are passed `payments` — never the data output. The fee (`needed_fee = fee_per_vbyte * vbytes`), the `TooLowFee` minimum-fee check, and the `TooLargeTransaction` weight check are all computed against a transaction that does not include the `OP_RETURN` output that is actually serialized and signed. This is the same bug class as the Blueberry double-fee finding — a fee-consistency break between two functions in the same withdrawal/send path — here manifesting as value accounted once in the outputs but never in the fee/weight accounting.

### Finding Description
In `SignableTransaction::new`, the `OP_RETURN` output is pushed onto `tx_outs` at `send.rs:194-202` before the weight is computed:

```rust
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}
```

Yet `calculate_weight_vbytes` is called with `payments` at `send.rs:204` and `send.rs:226`, and that helper builds its temporary `Transaction` solely from `payments` (`send.rs:85-99`). The result:

- `needed_fee` covers `fee_per_vbyte * vbytes` where `vbytes` excludes the `OP_RETURN` output (up to ~80 bytes of data plus ~43 bytes of output overhead).
- The `TooLowFee` check (`send.rs:211`) validates the understated vbytes, so the real fee rate `needed_fee / actual_vsize` can fall below `DEFAULT_MIN_RELAY_TX_FEE` and the transaction will be rejected by relaying nodes.
- The `TooLargeTransaction` check (`send.rs:241`) uses the understated `weight`, so a transaction padded with a data output can exceed `MAX_STANDARD_TX_WEIGHT` and produce a consensus-non-standard transaction that is never mined, while still consuming the selected inputs' fee budget.
- `fee()` (`send.rs:138-141`) reports `sum(inputs) - sum(outputs)` as the paid fee, while `needed_fee` advertises a value computed on a smaller transaction — the two "fee" figures diverge exactly like the duplicated-deduction inconsistency in the reference report.

The `data` parameter is a public input to `SignableTransaction::new`; any caller supplying untrusted or self-chosen bytes triggers the divergence.

### Impact Explanation
A transaction carrying a data output pays a fee rate lower than requested, potentially below the minimum relay fee, making it unrelayable and its inputs effectively frozen until rebuilt. In the worst case (near `MAX_STANDARD_TX_WEIGHT`), the constructed transaction exceeds standardness limits and will never confirm, while `needed_fee()` still reports a value the caller believes guarantees inclusion at `fee_per_vbyte`.

### Likelihood Explanation
Triggered whenever `data` is `Some` (up to 80 bytes are explicitly permitted by the `TooMuchData` check). The processor currently passes `None`, so the bug only fires for direct users of the wallet API who attach data — a supported, documented code path, not a misuse.

### Recommendation
Pass the fully constructed `tx_outs` (including the `OP_RETURN` output) into `calculate_weight_vbytes`, or compute weight/vbytes from the final `Transaction` after all outputs are added. Alternatively, refuse `data` when accuracy cannot be guaranteed, matching the simplicity rationale already used for `DUST`.

### Proof of Concept
```rust
// Construct payments + change such that the tx is just under MAX_STANDARD_TX_WEIGHT,
// then attach 80 bytes of data.
let data = Some(vec![0xaa; 80]);
let stx = SignableTransaction::new(inputs, &payments, change, data, fee_per_vbyte).unwrap();

// The real transaction contains the OP_RETURN output...
assert!(stx.transaction().output.iter().any(|o| o.script_pubkey.is_op_return()));

// ...but needed_fee was computed without it, so the effective fee rate is lower
// than fee_per_vbyte and may fall below the minimum relay fee.
let actual_vsize = stx.transaction().vsize() as u64;
assert!(stx.needed_fee() < fee_per_vbyte * actual_vsize);
```
Root cause: `send.rs:194-202` adds the output, while `send.rs:204` and `send.rs:226` compute weight from `payments` only.