### Title
OP_RETURN data output excluded from fee weight calculation lets transactions underpay the specified fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts an arbitrary `data` payload that is appended as a zero-value OP_RETURN output, but both invocations of `calculate_weight_vbytes` only account for `payments` (and optionally `change`). The up-to-83-byte OP_RETURN output is never included in the weight/vbytes estimate, so `needed_fee` is computed on a smaller virtual size than the transaction actually has. The result is a transaction that pays a lower effective fee rate than the caller requested — the same class as "users can avoid paying fees," where an attacker-controlled component escapes the fee accounting.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` pushes the OP_RETURN output onto `tx_outs` at lines 193–202, after computing `needed_fee` at line 206 via `calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204. `calculate_weight_vbytes` (lines 62–127) constructs a synthetic transaction whose outputs are built exclusively from `payments` plus an optional `change` output; the `data` OP_RETURN output is never modeled.

```rust
// networks/bitcoin/src/wallet/send.rs:204
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

let mut needed_fee = fee_per_vbyte * vbytes;
```

The `data` argument is caller-controlled and may be up to 80 bytes (line 171). An OP_RETURN output carrying 80 bytes adds ~90 bytes to the real transaction (~23 vbytes for a SegWit spend since output bytes are non-witness), none of which is priced. The change path has the same defect: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at lines 225–226 again omits the OP_RETURN output, so `fee_with_change` is also underpriced.

The actual fee paid is `sum(inputs) - sum(outputs)` (lines 138–141); since the OP_RETURN output has zero value, the sats paid equal the underpriced `needed_fee`, meaning the transaction's true fee rate is strictly below `fee_per_vbyte`.

### Impact Explanation
A user supplying the optional `data` field obtains a transaction that underpays relative to the requested fee rate — concretely avoiding a portion of the fee, matching the reported bug class. Additionally, the minimum-fee check at line 211 divides `DEFAULT_MIN_RELAY_TX_FEE` by the underestimated `vbytes`, so a transaction whose real size pushes it below the minimum relay fee rate can still pass validation and then be rejected/dropped by the Bitcoin network, leaving funds unspent longer than intended.

### Likelihood Explanation
The `data` parameter is a public input to `SignableTransaction::new` reachable by any caller constructing a payment with an OP_RETURN payload; no privileged position is required. The miscalculation is deterministic whenever `data` is `Some`.

### Recommendation
Include the OP_RETURN output in the weight calculation — e.g., pass the finalized `tx_outs` (or an extended `payments` slice including `(op_return_script, 0)`) into `calculate_weight_vbytes` for both the no-change and with-change estimates, and re-check `TooLowFee` against the accurate vbytes.

### Proof of Concept
```rust
// Conceptual: construct a SignableTransaction with an OP_RETURN payload
let data = vec![0xAA; 80];
let tx = SignableTransaction::new(
    inputs,                       // e.g. one ReceivedOutput covering payments + fee
    &[(payment_script, payment_amt)],
    Some(change_script),
    Some(data),                   // adds ~11-byte OP_RETURN script + 80-byte push
    fee_per_vbyte,
).unwrap();

// tx.weight() exceeds the vbytes used internally; verify:
// tx.transaction().weight().to_wu() / 4 > needed_fee / fee_per_vbyte
// i.e. tx.fee() == tx.needed_fee() yet the real vsize is larger,
// so the effective feerate < fee_per_vbyte.
```
Compare `tx.transaction().vsize()` (includes the OP_RETURN output) against `tx.needed_fee() / fee_per_vbyte` — the former exceeds the latter by roughly `data.len() + ~11` bytes worth of weight, confirming the output was never priced.