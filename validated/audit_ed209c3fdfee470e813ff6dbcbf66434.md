Let me look at the signing path in `send.rs` and related crypto to check for a stronger reachable analog.### Title
OP_RETURN data output omitted from transaction weight/vbyte fee calculation, producing underpriced transactions that cannot be fee-bumped - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an OP_RETURN output carrying caller-supplied `data` (up to 80 bytes) to `tx_outs`, but computes the transaction's weight and vbytes via `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which only models the `payments` outputs — never the OP_RETURN output. The result is that `needed_fee` and the minimum-relay-fee check are computed against a vsize smaller than the transaction's actual size. Analogous to the Shoebill incident, an interaction between two independently correct configuration inputs (the `data` payload and the `fee_per_vbyte` rate) produces an exploitable/incorrect outcome: the signed transaction pays a lower effective fee rate than requested and can fall below the network minimum relay fee, while `Sequence::MAX` on every input disables BIP-125 replace-by-fee, leaving no in-band remediation.

### Finding Description
`SignableTransaction::new` builds `tx_outs` from `payments`, then pushes an additional `TxOut` with `ScriptBuf::new_op_return(...)` when `data` is provided. The fee sizing then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, whose internal mock `Transaction` only includes the `payments` outputs (and optionally `change`). The OP_RETURN output — which is actually serialized into the final signed transaction — contributes roughly `8 (value) + 1 (script len) + 2 + data.len()` serialized bytes (~up to ~93 bytes, ~24 vbytes) that are never counted.

The same omission applies to the change-path recalculation at `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`.

Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` underestimates the fee required to hit the caller-specified rate. The actual fee paid (`sum(inputs) - sum(outputs)`, computed by `fee()`) is fixed by the (correct) change calculation, so the *effective* sat/vbyte is strictly lower than `fee_per_vbyte`.
2. The `TooLowFee` guard compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the same underestimated `vbytes`. With `data` present and a low `fee_per_vbyte` (e.g., 1 sat/vB, the protocol's stated minimum), the check can pass while the real feerate of the consensus transaction is below the 1 sat/vB relay minimum, so the signed transaction is rejected/banished from mempools.
3. All inputs are created with `sequence: Sequence::MAX`, which opts out of BIP-125 signaling, so the stuck transaction cannot be replaced; recovery requires coordinating the whole threshold multisig to sign a *conflicting* double-spend — a cold-path recovery, not a fee bump.

### Impact Explanation
Reachable via public input: `data` (the `InInstruction`/OP_RETURN payload in Serai's normal Bitcoin flow) and `fee_per_vbyte` are both attacker- or integrator-influenced inputs to `SignableTransaction::new`. A transaction produced by the honest threshold signing flow (`TransactionMachine`/`TransactionSignMachine`) can be unrelayable or systematically underpriced despite the caller specifying a valid fee rate. For a threshold-custody system, this translates to time-critical spends (refunds, rebalances, eventuality completions) silently producing transactions that do not confirm, and forced re-signing rounds; combined with no-RBF inputs, funds backing the inputs are effectively frozen until a full re-coordination. Severity: Medium — incorrect fee math causing stuck/unconfirmable protocol transactions, no direct theft.

### Likelihood Explanation
Any `SignableTransaction::new` call with `data.is_some()` triggers the undercount; the failure to relay additionally requires `fee_per_vbyte` low enough that the missing ~24 vbytes push the true rate below the relay minimum — precisely the regime the `TooLowFee` check is supposed to guard. Serai's own protocol attaches OP_RETURN data to Bitcoin transactions, so the buggy path is exercised in normal operation, not an edge case.

### Recommendation
Include the OP_RETURN output (and its serialized length, driven by `data.len()`) in `calculate_weight_vbytes`, e.g., pass `data`/`tx_outs` through rather than `payments`, for both the initial and with-change calculations. Alternatively, after constructing the final `tx`, compute `tx.vsize()` directly and recompute `needed_fee`, failing if the change output would have to shrink below `DUST`. Consider signaling RBF (`Sequence::ENABLE_RBF_NO_LOCKTIME`) to retain a fee-bump path.

### Proof of Concept
In `SignableTransaction::new`:

```rust
// networks/bitcoin/src/wallet/send.rs:193-204
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),  // added to real tx
  })
}
let (mut weight, vbytes) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, None); // OP_RETURN not modeled
```

`calculate_weight_vbytes` (lines 62-127) builds its mock `Transaction` with `output: payments.iter().map(...)` only. With `data` of 80 bytes, the actual signed transaction is ~93 serialized bytes (~24 vbytes) larger than `vbytes` implies. Call with `fee_per_vbyte = 1` and `data = vec![0; 80]`: `needed_fee` and the `TooLowFee` comparison both use the short `vbytes`, so construction succeeds and the multisig signs a transaction whose real feerate is `< 1` sat/vB — below `DEFAULT_MIN_RELAY_TX_FEE` — which Bitcoin Core rejects from relay, while `Sequence::MAX` inputs prevent replacement.