### Title
OP_RETURN `data` output is omitted from the fee/vsize estimate, so the declared fee rate is silently underpaid and change is inflated - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` computes the transaction's weight/vbytes — and therefore `needed_fee` and the change amount — over only the inputs and payments. The OP_RETURN output created from the optional `data` argument is pushed to `tx_outs` afterward and never included in either `calculate_weight_vbytes` call. Like the Vader IL bug, where the reimbursement formula used the *original* deposit amounts while ignoring the slip adjustment that reduced the LP's real claim, this code prices the transaction using a *smaller* transaction than the one actually produced: the declared `fee_per_vbyte` is applied to a vsize that is missing every byte of the OP_RETURN output. The missing cost is absorbed by the change output, which is inflated by `fee_per_vbyte * data_vsize` relative to a correctly priced transaction, while the effective fee rate falls below both the requested rate and the minimum-relay check the code performs.

### Finding Description
`SignableTransaction::new` accepts an optional `data` payload of up to 80 bytes and appends an OP_RETURN output for it at lines 193–202 of `networks/bitcoin/src/wallet/send.rs`. However, both weight/vbyte estimates are computed as `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204) and `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (lines 225–226) — the helper builds a mock transaction whose outputs are only `payments` plus an optional change output, so the OP_RETURN output's vsize is never counted.

The consequences chain together asymmetrically:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) is smaller than the true cost for the requested rate.
2. The `TooLowFee` check (lines 211–213) validates `needed_fee` against the *underestimated* vbytes, so a transaction that is actually below `DEFAULT_MIN_RELAY_TX_FEE` for its real size can pass.
3. `NotEnoughFunds` (lines 215–221) uses the underestimated fee, so a transaction can be built that cannot afford the real required fee — the shortfall is silently taken from change.
4. The change amount `value = input_sat - payment_sat - fee_with_change` (line 228) is inflated by exactly `fee_per_vbyte * data_vsize`, since `fee_with_change` is also computed without the OP_RETURN. The created transaction pays `fee_with_change` total but occupies `vbytes_with_change + data_vsize`, so its real fee rate is strictly below the caller's requested `fee_per_vbyte`.

`fee()` (lines 138–141) and `needed_fee()` (line 133) then report a fee that does not correspond to the actual transaction size, so any downstream logic that trusts `needed_fee()` (e.g., fee amortization across payments) operates on an incorrect figure.

### Impact Explanation
An unprivileged party who can cause a `data` payload to be included in a transaction (the API explicitly supports up to 80 bytes of caller-supplied data) produces transactions whose effective fee rate is lower than the protocol's configured rate — by up to roughly `(10 + 80) vbytes * fee_per_vbyte`. At fee rates near the relay minimum, the signed transaction can be rejected by the Bitcoin network despite passing the `TooLowFee` guard, stalling threshold-signed spends. Independently, the change output is overpaid at the expense of the declared fee budget — an asymmetric accounting error where the formula balances on inputs that do not reflect the actual transaction, directly analogous to IL reimbursement being computed on pre-slippage deposit values.

### Likelihood Explanation
Triggers deterministically whenever `SignableTransaction::new` is called with `Some(data)` and a change output; the magnitude scales with data length and `fee_per_vbyte`. Whether it causes relay failure depends on how close the requested rate is to the minimum, but the incorrect `needed_fee` and inflated change occur on every such call.

### Recommendation
Include the OP_RETURN output in the weight/vsize estimate: pass the full output list (payments + OP_RETURN + optional change) to `calculate_weight_vbytes`, or add the constant size of `OP_RETURN` + pushdata + value field to the computed weight before deriving `needed_fee` and the change amount.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// 1. inputs holds one ReceivedOutput worth 100_000 sats, payments empty,
//    change = Some(addr), data = Some(vec![0; 80]), fee_per_vbyte = 10.
let tx = SignableTransaction::new(inputs, &[], Some(change), Some(vec![0; 80]), 10).unwrap();

// 2. calculate_weight_vbytes was called with payments only (line 204 / 226);
//    needed_fee was priced for a 1-in/1-out tx, but the produced tx has an
//    extra ~90-byte OP_RETURN output (lines 193-202).
let actual_vsize = tx.transaction().vsize() as u64;
assert!(actual_vsize * 10 > tx.needed_fee()); // real cost > priced cost

// 3. The change output = input - fee_with_change, i.e. it is inflated by
//    10 * data_vsize, and the effective rate = needed_fee / actual_vsize < 10.
```

The relevant code is `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` (lines 150–256), specifically the `calculate_weight_vbytes` calls at lines 204 and 225–226 which omit the `data` output appended at lines 193–202.