### Title
`data=` OP_RETURN output is omitted from transaction fee/size accounting - (File: `networks/bitcoin/src/wallet/send.rs`)

### Summary
`SignableTransaction::new` accepts an optional `data` payload and adds it as an OP_RETURN output, but calculates both the initial and change-carrying transaction’s virtual size using only `payments` and `change`. The data output is therefore silently ignored by fee estimation and maximum-weight accounting.

### Finding Description
When `data` is supplied, the constructor appends a zero-valued OP_RETURN output to `tx_outs` at `networks/bitcoin/src/wallet/send.rs:193-202`. However, the transaction-size calculation is called with `payments`, not the final output list, at `send.rs:204`. The same omission occurs when deciding whether a change output is economical at `send.rs:224-233`, where `calculate_weight_vbytes` again receives only `payments` and the change script. As a result, every byte in the OP_RETURN output increases the serialized transaction size and weight but contributes nothing to `vbytes`, `needed_fee`, `fee_with_change`, or the final `weight` checked against the standard transaction limit.

This is analogous to accepting an API argument whose effect is not reflected in the operation’s accounting: the caller requests a data output and a fee rate, while the implementation silently prices the transaction as though that output did not exist.

### Impact Explanation
The generated transaction intentionally pays less than `fee_per_vbyte` for its actual virtual size. For an 80-byte payload—the maximum accepted at `send.rs:171-173`—the missing output adds roughly 90 serialized bytes, or approximately 23 vbytes. At low fee rates this can produce a transaction below the relay minimum even though the constructor’s `TooLowFee` check passed using the understated size. At higher sizes it also permits `weight` to exceed the checked standard limit because the OP_RETURN output’s weight is absent.

This is a medium-severity availability/accounting issue: callers can produce transactions that fail relay or are delayed despite requesting an adequate fee, and the displayed `needed_fee()` / `fee()` values do not describe the transaction that was actually created.

### Likelihood Explanation
The issue is deterministic whenever `SignableTransaction::new` receives a non-empty `data` value. An unprivileged caller able to cause the wallet to construct a data-carrying transaction can trigger it with public transaction data; no malformed cryptographic input, malicious validator, or private-key access is required. The amount of underpayment scales with payload length and requested fee rate.

### Recommendation
Calculate size and weight from the complete output set, including the OP_RETURN output. In particular:

- Pass `&tx_outs`-equivalent output descriptions to `calculate_weight_vbytes`, or otherwise include the serialized OP_RETURN output in its model.
- Repeat the complete calculation when evaluating the optional change output.
- Check `TooLowFee`, change creation, and `TooLargeTransaction` against the final transaction’s actual size/weight.
- Add tests for transactions with `data`, both with and without change, asserting `fee() >= fee_per_vbyte * actual_vbytes` and that the standard-weight check sees the OP_RETURN bytes.

### Proof of Concept
```rust
let data = vec![0x42; 80];
let tx = SignableTransaction::new(
  vec![received_output],
  &[(payment_script, DUST)],
  Some(change_script),
  Some(data),
  fee_per_vbyte,
).unwrap();

let actual_vbytes = /* vbytes of tx.transaction(), or reconstructed
                       final transaction including OP_RETURN */;
let paid_fee = tx.fee();

assert!(paid_fee < fee_per_vbyte * actual_vbytes);
```

The fee calculation is performed before the OP_RETURN-containing `tx_outs` is used to construct the returned transaction, so the actual transaction is larger than the transaction represented by the fee calculation.