### Title
`SignableTransaction::new` computes the fee from a transaction weight that excludes the OP_RETURN data output, underpaying relative to the requested feerate — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to `getMaxDeposit()` using `1e18` where the true "unit" was `10**pairToken.decimals()` — i.e. computing a value against a wrong scale/basis — `SignableTransaction::new` computes `needed_fee` and `vbytes` from a transaction built only out of `payments`, while the actual signed transaction additionally contains the OP_RETURN output carrying up to 80 bytes of caller-supplied `data`. The fee therefore corresponds to a smaller transaction than the one that is actually broadcast and signed.

### Finding Description
`SignableTransaction::new` pushes an OP_RETURN `TxOut` into `tx_outs` when `data` is supplied (lines 194–202), but then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204). `calculate_weight_vbytes` reconstructs a `Transaction` whose `output` vector is built exclusively from `payments` (lines 85–99); the OP_RETURN output is never included in the measured transaction. The same omission occurs on the change path, where `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (lines 225–226) again measures only `payments` plus change.

`needed_fee = fee_per_vbyte * vbytes` (line 206) therefore prices a transaction that is roughly `8 (value) + 1 (script len) + ~2–10 (OP_RETURN + pushdata) + data.len()` base bytes smaller than the real one — up to ~91 vbytes of unaccounted weight for a maximal 80-byte payload. The change output value (line 228) and `needed_fee` are both derived from this under-measured size, so the produced transaction's actual feerate is strictly below the caller-requested `fee_per_vbyte`. The minimum-relay-fee check at line 211 also validates `needed_fee` against the understated `vbytes`, so it can pass a transaction whose real feerate falls below the relay minimum when `data` is large.

### Impact Explanation
An unprivileged caller who supplies `data` (e.g. a processor/integrator flow attaching batch metadata) causes the vault/multisig to produce and sign a transaction paying a lower effective feerate than requested. At best this yields an unintended fee (funds leaving the wallet that were not priced correctly); at worst, when `fee_per_vbyte` is near the relay minimum, the signed transaction silently falls below `DEFAULT_MIN_RELAY_TX_FEE` on its real vsize and is rejected by the network, stalling a spend the threshold group believed was correctly priced. `needed_fee()` and `fee()` report inconsistent values for the same intent, which is exactly the "wrong result from a conversion/scaling routine" class of the original report.

### Likelihood Explanation
Reachable whenever `SignableTransaction::new` is invoked with `data.is_some()`; the miscalculation is deterministic (the OP_RETURN size is never counted), though the economic impact scales with `fee_per_vbyte` and the data length. The deviation is bounded (~91 vbytes), so this is a correctness/Medium issue rather than a fund-theft primitive.

### Recommendation
Pass the fully-constructed output list into `calculate_weight_vbytes` instead of `payments`, e.g. compute `(weight, vbytes)` over `tx_outs` (and `tx_outs` + change on the change path), so `needed_fee`, the change amount, and the minimum-fee check all reflect the transaction that is actually signed.

### Proof of Concept
```rust
// Conceptual: a payment + 80-byte OP_RETURN, fee_per_vbyte = 1
let data = vec![0u8; 80];
let tx = SignableTransaction::new(inputs, &payments, None, Some(data.clone()), 1).unwrap();
// tx.needed_fee() was priced without the ~91-vbyte OP_RETURN output.
// Actual vsize of tx.transaction() exceeds the measured vbytes by ~91,
// so the real feerate is ~needed_fee / (vbytes + 91) < 1 sat/vbyte.
```
Concretely: with `fee_per_vbyte` chosen so `needed_fee` just satisfies `(DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000`, the real transaction — carrying the extra OP_RETURN output — has a feerate below the relay minimum and will not propagate, despite `new` returning `Ok`.