### Title
OP_RETURN data output is excluded from fee/vsize calculation, producing underpriced transactions - (networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes `needed_fee` and the minimum-relay-fee check from a vsize estimate built only from `payments` (and optionally `change`), omitting the OP_RETURN `data` output that is actually appended to `tx_outs`. Analogous to the fee-on-transfer report — where a withdrawal promises N units but the mechanism delivers less than the accounting expects — the transaction "promises" `fee_per_vbyte` but the actual serialized transaction pays a strictly lower fee rate. Transactions carrying data are systematically underpriced and can be produced that fail Bitcoin's minimum relay fee, leaving signed withdrawal/transfer transactions unbroadcastable.

### Finding Description
In `SignableTransaction::new`, `tx_outs` is extended with the OP_RETURN output at `send.rs:194-202`, but the fee math at line 204 calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, and the change path at line 226 calls `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`. Inside `calculate_weight_vbytes` (lines 62-127), the mock transaction's outputs are built solely from `payments` plus the optional change output — the `data` output is never modeled. Consequently:

- `needed_fee = fee_per_vbyte * vbytes` uses a vbytes that excludes up to ~95 vbytes (80-byte OP_RETURN payload plus output overhead, limited by the `TooMuchData` check at line 171).
- The minimum relay check at line 211 uses the same undercounted `vbytes`, so a transaction whose true fee rate is below `DEFAULT_MIN_RELAY_TX_FEE` can pass the `TooLowFee` check.
- `fee()` (lines 138-141) proves the actual fee is `sum(prevouts) - sum(outputs)`, which exactly equals the undercounted `needed_fee` — the deficit is silently absorbed by the fee, not detected.

Just as the Notional withdrawal reverts because the contract expects 105 USDT but receives 104.9, here the transaction is constructed expecting `fee_per_vbyte * actual_vsize` but only commits `fee_per_vbyte * (actual_vsize - data_vsize)`.

### Impact Explanation
Any caller constructing a `SignableTransaction` with `data: Some(...)` produces a transaction paying less than the specified fee rate. With an 80-byte payload the shortfall is ~95 vbytes worth of fee. If `fee_per_vbyte` is near the relay minimum, the resulting signed transaction is below `DEFAULT_MIN_RELAY_TX_FEE` and will be rejected by the peer-to-peer network — funds become unmovable through that transaction and any accounting/eventuality keyed to its txid never completes, a liveness failure analogous to the withdrawal DoS in the source report. Severity: Medium, since the defect requires the `data` field to be populated (Serai's processor currently passes `None`, but the field is public API intended for instruction-carrying transactions).

### Likelihood Explanation
Reachable by an unprivileged party whenever transaction data flows into `SignableTransaction::new` — the `data` parameter is exactly the untrusted "transaction data they cause to be signed" input class. Within the repo's own processor call site (`processor/src/networks/bitcoin.rs:446-452`) `data` is `None`, so the defect is latent in current Serai usage; it triggers for any integrator or future code path attaching OP_RETURN data, which the API explicitly supports.

### Recommendation
Include the OP_RETURN output in the weight estimation: pass the full `tx_outs` (or construct the data output inside `calculate_weight_vbytes`) rather than `payments`, for both the no-change and change calculations. Alternatively, compute `vbytes` from the final `tx.output` list so any future output type cannot be omitted again.

### Proof of Concept
In `networks/bitcoin/src/wallet/send.rs`, call `SignableTransaction::new(inputs, payments, change, Some(vec![0; 80]), fee_per_vbyte)` with `fee_per_vbyte` chosen so `fee_per_vbyte * vbytes` is just above `(DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` for the undercounted `vbytes` but below it for the true vsize (add ~95). The constructor returns `Ok` (no `TooLowFee`), yet `tx.fee() / tx.tx.vsize()` is below the relay minimum and `send_raw_transaction` rejects it. `assert!(tx.tx.weight().to_wu() > weight_used_for_fee)` demonstrates the accounting discrepancy directly.

Key code: `send.rs:204` (vbytes excludes data), `send.rs:194-202` (OP_RETURN appended), `send.rs:211` (min-fee check on wrong vbytes), `send.rs:85-93` (outputs built only from `payments`).