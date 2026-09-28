### Title
`SignableTransaction::new` omits the OP_RETURN data output from weight/vbyte and fee accounting - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to `getOracleData()` computing `maxExternalDeposit = supplyCap - aTokenSupply` while omitting the `accruedToTreasury` term, `SignableTransaction::new` computes the transaction's weight, virtual size, `needed_fee`, minimum-relay-fee check, and `MAX_STANDARD_TX_WEIGHT` check from `payments` only, omitting the attacker-influenced OP_RETURN `data` output it has already decided to append. The result is a bound that is off by the size of an output the caller controls.

### Finding Description
`SignableTransaction::new` accepts `data: Option<Vec<u8>>` of up to 80 bytes and appends an OP_RETURN `TxOut` to `tx_outs` (send.rs:194-202). However, all sizing math is performed by `calculate_weight_vbytes(tx_ins.len(), payments, change)` (send.rs:204, 225-226), which builds the mock transaction strictly from `payments` plus the optional change output (send.rs:85-99). The `data` output is never included.

Consequences of the missing term:
- `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) and `fee_with_change` (send.rs:227) are underpriced by `fee_per_vbyte * (size of OP_RETURN output)` (~10-90+ vbytes). The minimum-relay-fee check at send.rs:211 is also evaluated against the understated vbytes.
- The change output value is computed as `input_sat - payment_sat - fee_with_change` (send.rs:228), so change is inflated by exactly the unpaid fee for the data output, and the real fee rate ends up below `fee_per_vbyte`.
- The standardness check `weight > MAX_STANDARD_TX_WEIGHT` (send.rs:241) uses `weight` that excludes the data output, so a transaction can pass this check while its actual weight exceeds the standardness limit once the OP_RETURN output is included.

An unprivileged party supplying the `data` bytes (e.g., arbitrary instruction/metadata bytes attached to an external output) directly controls the size of the omitted term.

### Impact Explanation
The constructed transaction pays a lower effective fee rate than requested and can be non-standard (over `MAX_STANDARD_TX_WEIGHT`) while passing all internal checks. Like the Aave report where the inflated `maxExternalDeposit` causes `rebalance()` to fail, a transaction produced here may fail to propagate or confirm, stalling the funds/change it carries. The impact is a liveness/fee-accounting failure rather than direct theft; the misaccounted amount is bounded by ~80 bytes of witness-free data, consistent with Medium severity.

### Likelihood Explanation
The bug triggers deterministically whenever `data` is `Some` — every call path that attaches an OP_RETURN output gets systematically understated fee/weight accounting. No special timing or adversary positioning is required; supplying max-size data maximizes the discrepancy.

### Recommendation
Include the OP_RETURN output in the weight/vbyte estimate: change `calculate_weight_vbytes` to take the full `tx_outs` list (or accept `data: Option<&[u8]>` and append the OP_RETURN `TxOut` inside the mock transaction), and call it after `tx_outs` has been extended with the data output at send.rs:204 and 225-226.

### Proof of Concept
```rust
// Conceptual: construct a SignableTransaction whose `data` is 80 bytes.
let data = vec![0xaa; 80];
let tx = SignableTransaction::new(inputs, &payments, change, Some(data), fee_per_vbyte)
    .unwrap();

// The internally estimated vbytes never counted the OP_RETURN output.
// Actual signed transaction:
let actual_vbytes = tx.transaction().vsize() as u64;
let actual_fee = tx.fee(); // sum(inputs) - sum(outputs)

// actual_fee / actual_vbytes < fee_per_vbyte, and if payments+data push the real
// weight past MAX_STANDARD_TX_WEIGHT, the TooLargeTransaction check still passed.
assert!(actual_vbytes > (tx.needed_fee() / fee_per_vbyte));
```
The mock transaction in `calculate_weight_vbytes` (send.rs:85-99) demonstrably contains only `payments` and `change`, while the final `Transaction` (send.rs:246-251) additionally contains the OP_RETURN output — a missing term identical in class to the omitted `accruedToTreasury`.