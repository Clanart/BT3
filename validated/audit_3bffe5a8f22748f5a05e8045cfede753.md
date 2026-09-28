I'll analyze the Serai codebase for an analog to this bug class: an offset/safety-margin formula computed from only one component (ignoring others), rounded in the wrong direction, or estimated on an aggregate rather than the worst-case part.The Bitcoin wallet fee/change math is a promising analog. Let me read the full `send.rs` and check related scanner/offset code.The fee/weight calculation in `SignableTransaction::new` looks like a strong analog — it computes vbytes from `payments` only, excluding the OP_RETURN `data` output that is actually added to `tx_outs`. Let me verify reachability — who calls `SignableTransaction::new` and with what `data`.### Title
`SignableTransaction::new` computes weight/vbytes and `needed_fee` from `payments` only, omitting the OP_RETURN `data` output that is actually included in the transaction — ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
Analogous to M-15 (where the liquidity offset was derived from only `amount1` while the mint also needed `amount0`, and estimated on the aggregate range rather than the worst-case part), `SignableTransaction::new` derives its fee/weight safety margin from only a subset of the outputs that will actually appear in the transaction. `calculate_weight_vbytes` is invoked with `payments` and `change`, but the OP_RETURN output built from `data` is pushed into `tx_outs` before the measurement and is never included in any `calculate_weight_vbytes` call. The result is that `needed_fee`, the `MAX_STANDARD_TX_WEIGHT` check, and the change-amount computation are all evaluated against a smaller transaction than the one produced and signed.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` at `send.rs:194-202`, yet the weight/vbyte estimation at `send.rs:204` is `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — passing `payments`, not `tx_outs`. The change-handling path repeats the same omission at `send.rs:225-226` (`calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`). `calculate_weight_vbytes` itself (`send.rs:62-99`) only builds `tx.output` from the slice it is given plus the optional change script, so it faithfully measures what it is handed — the callers simply hand it an incomplete output list.

Concretely, when `data = Some(d)`:
- The real transaction contains an extra `TxOut` of ~8 (value) + 1 (script length) + 1–2 (OP_RETURN + push opcode) + `d.len()` bytes — up to ~91 vbytes with the 80-byte `data` cap checked at `send.rs:171`.
- `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`) is `fee_per_vbyte * (~91)` satoshis too small relative to the requested feerate.
- `fee()` (`send.rs:138-141`) confirms the paid fee equals `needed_fee` exactly (change absorbs the rest), so the *effective* feerate is `needed_fee / actual_vbytes < fee_per_vbyte`.
- The `TooLowFee` check at `send.rs:211` compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the underestimated `vbytes`, so a transaction whose true feerate falls below the minimum relay feerate still passes.
- The `TooLargeTransaction` check at `send.rs:241` uses `weight` that excludes the data output's weight.

This mirrors the M-15 pattern precisely: a margin formula that must cover *all* components is computed from only one, the estimate is made on an idealized aggregate (the payment-only tx) rather than the actual composite structure, and the failure mode is that the operation proceeds with an insufficient margin and produces a transaction that does not satisfy the parameter it was constructed under.

### Impact Explanation
When `data` is set, every transaction produced pays a strictly lower feerate than the `fee_per_vbyte` requested by the caller — underpaying by up to ~`91 * fee_per_vbyte` satoshis' worth of feerate. At or near the minimum relay feerate (1 sat/vB), a large `data` payload drops the *actual* feerate below `DEFAULT_MIN_RELAY_TX_FEE`, producing a signed transaction that standard Bitcoin nodes will not relay or mine — core spend/aggregation functionality silently broken for exactly the data-bearing cases, the same "legitimate attempts always fail" profile as M-15. At higher feerates the transaction still relays but confirms slower than specified and, in fee-bumping contexts, may anchor below the intended rate. Additionally, a boundary transaction could pass `MAX_STANDARD_TX_WEIGHT` while the real transaction marginally exceeds standardness weight. No direct theft of funds occurs — the fee is still `inputs − outputs` — so this is a Medium availability/correctness finding, matching M-15's severity class.

### Likelihood Explanation
The bug triggers deterministically whenever `data.is_some()` — no adversarial arrangement is needed beyond a caller supplying OP_RETURN data, which the API explicitly supports (`send.rs:149,171`). Reachability by an unprivileged party is through transaction data they cause to be signed; the `data` parameter flows into `SignableTransaction::new` (called from `processor/src/networks/bitcoin.rs`) for data-embedding transactions. The revert-equivalent outcome (unrelayable TX) requires `data` near the 80-byte cap combined with `fee_per_vbyte` near the protocol minimum — plausible but narrower, analogous to M-15 requiring specific tick ranges. Any `data` at any feerate still produces the understated-feerate transaction, so the incorrect-formula behavior is universal for data-bearing sends. One caveat I could not fully verify within the available iterations: whether the processor currently invokes `SignableTransaction::new` with non-`None` `data` on production paths; the defect exists in the library regardless, and triggers for any such caller.

### Recommendation
Pass the complete output set into the estimator. Either:
- call `Self::calculate_weight_vbytes(tx_ins.len(), &tx_outs_as_pairs, change)` after the OP_RETURN push (restructure so the payment list includes the data output), or
- change `calculate_weight_vbytes` to accept the finalized `&[TxOut]` (`tx_outs`) and the optional change script, eliminating the possibility of measuring a different output set than the one signed.

Apply the same fix to the change-path call (`send.rs:225-226`) so `fee_with_change`, `weight`, and the `NotEnoughFunds`/`TooLargeTransaction` checks all reflect the real transaction — the analog of computing `max(L0, L1)` over the true worst-case segment rather than the aggregate range.

### Proof of Concept
```rust
// In networks/bitcoin/tests/wallet.rs — demonstrates the underestimated fee.
// With data = 80 bytes and fee_per_vbyte = 1 (the minimum that passes TooLowFee):
let data = vec![0u8; 80];
let tx = SignableTransaction::new(
    vec![received_output],                       // one P2TR input
    &[(payment_script, 10_000)],                 // one payment above DUST
    Some(change_script),                         // change enabled
    Some(data),                                  // OP_RETURN output included
    1,                                           // fee_per_vbyte at min relay
).unwrap();

// needed_fee was computed WITHOUT the OP_RETURN output (send.rs:204).
// The real transaction (tx.transaction()) includes it (send.rs:194-202),
// so its true vsize exceeds the estimated `vbytes` by ~91.
let actual_vsize = tx.transaction().vsize() as u64;
let paid_fee = tx.fee(); // == needed_fee
// paid_fee < actual_vsize * 1  =>  feerate < 1 sat/vB
assert!(paid_fee < actual_vsize); // below DEFAULT_MIN_RELAY_TX_FEE -> unrelayable
```

Trace equivalent to the M-15 `STF` revert: `send.rs:194-202` pushes the OP_RETURN `TxOut`; `send.rs:204` measures weight using `payments` (no data); `send.rs:206` sets `needed_fee = fee_per_vbyte * vbytes` (underestimated); `send.rs:211` passes the min-fee check against the too-small `vbytes`; `send.rs:225-232` commits change using `fee_with_change` computed from the same incomplete output list. The signed transaction is then larger than measured and underpays its specified feerate — broadcasting fails minimum-fee relay policy at low feerates, exactly paralleling the insufficient-liquidity-offset revert.