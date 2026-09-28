### Title
Fee and weight are calculated before the OP_RETURN output is added, so the transaction is built and bounds-checked with stale values - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
In `SignableTransaction::new`, the OP_RETURN data output is pushed onto `tx_outs` before the transaction weight/vbytes are calculated, yet `calculate_weight_vbytes` is invoked with only `payments` — it never accounts for the data output. Both the initial fee estimate and the change-aware recalculation use this stale weight, so `needed_fee` systematically under-prices the transaction, and the `MAX_STANDARD_TX_WEIGHT` check runs against a weight smaller than the real transaction's.

### Finding Description
`SignableTransaction::new` builds the outputs in this order:

```rust
// networks/bitcoin/src/wallet/send.rs
// Add the OP_RETURN output
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` reconstructs a scratch `Transaction` whose `output` vector contains only `payments` plus an optional `change` — the OP_RETURN output already committed to `tx_outs` is omitted:

```rust
output: payments
  .iter()
  .map(|payment| TxOut { ... })
  .collect(),
```

The change path recomputes weight the same way (`Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`), so the OP_RETURN output is excluded there too. The final standardness check then runs on the stale `weight`:

```rust
if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
  Err(TransactionError::TooLargeTransaction)?;
}
```

The constructed `SignableTransaction` keeps the larger `tx_outs` (including the OP_RETURN output) while `needed_fee` and the weight bound reflect the smaller pre-mutation state — the exact shape of the Ajna bug: a derived value (LUP / weight & fee) computed before a debt/output-increasing mutation and never recalculated afterward.

### Impact Explanation
- **Systematic fee underpayment:** every transaction carrying `data` pays `fee_per_vbyte * vbytes` where `vbytes` excludes the OP_RETURN output (~90+ WU, up to ~360 WU for 80-byte data). The actual fee `sum(inputs) − sum(outputs)` is correspondingly below the requested rate, biasing confirmation dynamics — directly analogous to the systematically biased LUP/EMA in the report.
- **Standardness bound bypass:** a transaction whose true weight exceeds `MAX_STANDARD_TX_WEIGHT` (400,000 WU) passes the check because `weight` was computed without the OP_RETURN output. The multisig then signs and broadcasts a non-standard transaction that relays reject, so the plan's funds never move even though a valid signature was produced. The `data` field is attacker-influenced (arbitrary instruction data up to 80 bytes), and input/output counts near the bound are reachable via many-input plans, making the stall reachable with public inputs.

### Likelihood Explanation
Fee underpayment happens on *every* transaction with a `data` payload — no special conditions. The weight-overflow stall requires the payments+inputs weight to land within ~400 WU of the standard limit, which is narrow but reachable for max-sized plans; no malicious validator, leaked key, or misuse is required, only untrusted `data` bytes an unprivileged user can supply.

### Recommendation
Compute weight and fee from the actual `tx_outs` (including the OP_RETURN output), or pass the data output into `calculate_weight_vbytes` alongside `payments`, and re-derive `weight`/`needed_fee` after all outputs are final — mirroring the upstream fix of recalculating the final value after all mutations.

### Proof of Concept
1. Call `SignableTransaction::new` with inputs, `payments`, `change: Some(addr)`, and `data: Some(vec![0u8; 80])`.
2. Compare `signable.transaction().weight()` (includes the OP_RETURN output) against the `weight`/`vbytes` used internally: `needed_fee()` equals `fee_per_vbyte * vbytes(payments only)`, while `fee()` reflects the true output set — the realized fee rate is strictly below `fee_per_vbyte`.
3. For the bound bypass: choose `inputs`/`payments` such that `calculate_weight_vbytes(inputs, payments, change)` is just under `MAX_STANDARD_TX_WEIGHT`; adding an 80-byte OP_RETURN pushes the real transaction over the limit, yet `new` returns `Ok` and `multisig`/`preprocess`/`sign` produce a signature for a transaction no relay will accept.

Caveat: I was not able to fully trace how `data` flows from on-chain instructions into `SignableTransaction::new` within the iteration budget; the path via `Plan`/`InInstruction` data is implied by the API surface but not line-verified end to end.