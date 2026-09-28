### Title
OP_RETURN `data` output excluded from weight/vbyte calculation causes under-charged fee and over-credited change - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends the `data` (OP_RETURN) output to `tx_outs` before computing the transaction fee, but calls `calculate_weight_vbytes` with only `payments` as the output set. The fee is therefore calculated for a transaction smaller than the one actually built, and the missing sats are silently absorbed by / credited to the change output — the fee is effectively charged to the wrong party (change recipient instead of the miners), directly analogous to the MEME20 bug where fees were deducted from the wrong account.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` when `data` is specified (lines 194–202), but the weight is then computed via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204), which expands a template `Transaction` whose `output` vector contains only `payments` — the OP_RETURN output is never included. The same omission occurs for the change-enabled fee path (`calculate_weight_vbytes(..., Some(&change))`, line 226). An OP_RETURN output carrying up to 80 bytes adds roughly 90+ vbytes that are never priced. The change amount is computed as `input_sat - (payment_sat + fee_with_change)` (line 228), so the underpriced fee inflates the change output by exactly the missing amount: funds that should have been paid as fee are instead distributed to the change address. Additionally, the `TooLowFee` minimum-relay check (line 211) and `needed_fee`/`fee()` reporting are all computed against the understated size, so a caller requesting `fee_per_vbyte` gets a transaction whose real feerate is strictly lower than requested — and potentially below the default minimum relay feerate, since the check passes on the smaller, fictitious size.

### Impact Explanation
The transaction is broadcast paying a lower feerate than the caller specified, which can leave it unconfirmed or rejected by relay policy (funds temporarily unspendable), and change outputs are systematically over-valued while the network fee is under-paid — an incorrect distribution of funds of the same class as the reported issue. `fee()` and `needed_fee()` also misreport the true economics of the transaction to downstream accounting. Severity: Medium — it corrupts fee/change accounting and can produce non-propagating transactions, but does not directly hand funds to an attacker.

### Likelihood Explanation
The bug triggers deterministically whenever `SignableTransaction::new` is called with `Some(data)` — a public, documented argument of the constructor ("If data is specified, an OP_RETURN output will be added with it", line 149) with explicit support for up to 80 bytes (line 171). No special conditions are needed; every data-carrying transaction misprices its fee. The effect grows with the size of `data`.

### Recommendation
Include the OP_RETURN output in the template transaction inside `calculate_weight_vbytes` (e.g., extend the `payments`/outputs it expands, or append a `TxOut` with the OP_RETURN `script_pubkey`), so both the `needed_fee` calculation and the `fee_with_change` change deduction price the actual transaction being constructed. The `TooLowFee` check must then evaluate against the true vsize.

### Proof of Concept
```rust
// From networks/bitcoin/src/wallet/send.rs
// tx_outs gains the OP_RETURN output (lines 194-202):
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(/* up to 80 bytes */),
  })
}
// but the fee basis excludes it (line 204):
let (mut weight, vbytes) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
// and so does the change path (line 226):
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
// change keeps the un-priced sats (line 228):
let value = input_sat.checked_sub(payment_sat + fee_with_change);
```

Constructing `SignableTransaction::new(inputs, &payments, Some(change), Some(vec![0; 80]), fee_per_vbyte)` yields a `needed_fee()` and change amount identical to the `data: None` case, while the serialized transaction is ~90 vbytes larger — proving the OP_RETURN output's weight is never charged and its cost is instead redirected into the change output.