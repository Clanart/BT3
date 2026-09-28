### Title
`SignableTransaction` computes transaction weight/fee and the standard-weight bound from the payment outputs only, excluding the OP_RETURN `data` output it already added — a transaction that exceeds `MAX_STANDARD_TX_WEIGHT` can be constructed and signed yet never relay — ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
The Olympus bug priced an LP token by dividing scaled reserves by an *unscaled* supply: a derived denominator (`poolSupply`) was computed in a different domain than the numerator. In `bitcoin-serai`, `SignableTransaction::new` has the same structural flaw: the OP_RETURN `data` output is pushed into `tx_outs` (the real transaction) *before* the weight/vsize is measured, but `calculate_weight_vbytes` is invoked with `payments` — which does not include the data output. The resulting `weight` (used for the `TooLargeTransaction` standardness check) and `vbytes` (used for `needed_fee`, the min-relay-fee check, and the change amount) all correspond to a smaller transaction than the one actually built and signed.

### Finding Description
`SignableTransaction::new` first appends the OP_RETURN output to `tx_outs` when `data` is present:

```rust
// networks/bitcoin/src/wallet/send.rs:194-202
if let Some(data) = data {
  tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })
}
```

but then sizes the transaction from `payments` alone:

```rust
// networks/bitcoin/src/wallet/send.rs:204
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` builds the measurement transaction's `output` vector exclusively from `payments` (lines 85-93) plus an optional change output (lines 95-99). The OP_RETURN output is therefore invisible to:

1. `weight` — the `MAX_STANDARD_TX_WEIGHT` check at line 241. A tx whose true weight is up to `MAX_STANDARD_TX_WEIGHT + sizeof(OP_RETURN output)` (~ (8 + 1 + 2 + 80) * 4 ≈ 364 WU more) passes the check but is non-standard and will not be relayed/mined by default policy.
2. `vbytes` — `needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change` (line 227) understate the size, so the *effective* feerate of the signed transaction is below the requested `fee_per_vbyte`.
3. The `TooLowFee` check at line 211, which compares against the underestimated `vbytes`, so a transaction that doesn't meet `DEFAULT_MIN_RELAY_TX_FEE` on its true size can be accepted.

### Impact Explanation
The processor signs and broadcasts the transaction believing it paid `fee_per_vbyte`; in reality the feerate is lower and, at the boundary, the transaction exceeds the standard weight limit and cannot propagate. Inputs remain owned, but the funds become stuck pending re-signing with a smaller transaction — an availability/consensus-of-intent failure driven entirely by transaction data (`data` up to 80 bytes is attacker-influenceable where instructions carry payloads). This mirrors the Olympus impact: the derived denominator (tx size vs. pool supply) was not recomputed from the actual objects included.

### Likelihood Explanation
Any call to `SignableTransaction::new` with `data: Some(_)` triggers it; the miscalculation is unconditional, not adversary-dependent. Whether it crosses a standardness/feerate boundary depends on how close the payment set already is to the limits, so real-world impact is intermittent — Medium.

### Recommendation
Measure the transaction from the same output list that will be broadcast: either push the OP_RETURN output into the measurement (pass `tx_outs` or include the data output in the synthesized outputs inside `calculate_weight_vbytes`), or compute weight/vbytes after the full `tx_outs` vector is finalized — including when deciding change (the change-fee and change-dust evaluation at lines 224-235 must also price the data output).

### Proof of Concept
Construct `SignableTransaction::new` with one input, a payment set whose weight is just below `MAX_STANDARD_TX_WEIGHT`, `data: Some(vec![0; 80])`, and a `change` script. `calculate_weight_vbytes` reports a weight under the limit, `new` returns `Ok`, yet `tx.weight()` of the produced `tx` exceeds `MAX_STANDARD_TX_WEIGHT` because the OP_RETURN output is present in `tx.output` but absent from the measurement. Similarly, `needed_fee / actual_vsize < fee_per_vbyte` whenever `data` is non-empty.