### Title
OP_RETURN data output is appended to `tx_outs` but never included in the fee/weight accounting, so the `TooLowFee` and `TooLargeTransaction` checks are evaluated against a smaller transaction than the one actually signed - ([File: networks/bitcoin/src/wallet/send.rs](https://github.com/blackvul/serai--002/blob/main/networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the report's "checked value is never updated" bug class (`allocator.voiceCredits` never incremented, so `maxVoiceCreditsPerAllocator` never binds), `SignableTransaction::new` adds an OP_RETURN output to `tx_outs` but computes `weight`/`vbytes` — which drive both the minimum-fee check and the `MAX_STANDARD_TX_WEIGHT` check — solely from `payments`, so the added output is never charged for.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` at lines 194–202. The weight/size used for fee accounting is then computed at line 204 via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, where `payments` is the caller-supplied slice — the OP_RETURN output already present in `tx_outs` is not part of it. The same omission repeats for the change path at line 226 (`payments, Some(&change)`). Consequently:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change` (line 227) are underestimated by the serialized size of the OP_RETURN output (8-byte value + compactsize + script header + up to 82 bytes of script ≈ ~95 bytes).
- The minimum-relay-fee check at line 211 passes for transactions whose *effective* fee rate is below the default minimum.
- The `MAX_STANDARD_TX_WEIGHT` check at line 241 is evaluated on a lighter transaction than the one actually produced, so a tx that is in fact non-standard can be constructed and signed without error.

The constructed `Transaction` returned in `SignableTransaction` does contain the OP_RETURN output (line 250 uses the full `tx_outs`), so the discrepancy between the checked weight and the actual weight is real.

### Impact Explanation
An unprivileged party supplying the `data` parameter (or triggering a code path that supplies it) causes the threshold multisig to sign a transaction that pays less per vbyte than `fee_per_vbyte` requested — potentially below the mempool minimum relay fee — or that exceeds `MAX_STANDARD_TX_WEIGHT` while the code believes it is standard. The signed transaction may then be rejected by relay/miners, leaving the spent inputs' funds locked until the transaction is rebuilt. This is a violation of the protocol's own fee/size invariant: the accounting was checked against a value that was never updated to include the output the function itself added.

### Likelihood Explanation
Triggers whenever `SignableTransaction::new` is called with `data` set and the resulting transaction's effective fee rate or true weight crosses a boundary (min relay fee, standardness limit). It does not corrupt funds directly, but produces transactions the network will not relay. Severity: Medium — fee/size invariant violation with a concrete availability consequence for locked UTXOs; no theft or key compromise.

### Recommendation
Include the OP_RETURN output in the size accounting, e.g. by pushing it into a combined `outputs` slice passed to `calculate_weight_vbytes`, or by calling `calculate_weight_vbytes` after building the complete `tx_outs` list:

```rust
// build tx_outs (payments + OP_RETURN) first
let (mut weight, vbytes) =
    Self::calculate_weight_vbytes(tx_ins.len(), &tx_outs_as_payment_pairs, None);
```

Alternatively, have `calculate_weight_vbytes` take `&[TxOut]` directly so every output appended to the final transaction is necessarily counted.

### Proof of Concept
Construct `SignableTransaction::new(inputs, payments, change, Some(vec![0u8; 80]), fee_per_vbyte)` where `fee_per_vbyte` is chosen such that `needed_fee` passes line 211 but `needed_fee / actual_vbytes < min_relay_rate`. The returned `SignableTransaction.tx` serializes ~95 bytes larger than the `vbytes` used in the fee check, so `tx.weight()` exceeds the checked `weight` by the OP_RETURN's size, demonstrating the check was performed against a value the code never incremented.