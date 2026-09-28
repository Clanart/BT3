### Title
`SignableTransaction::new` omits the `OP_RETURN` data output from its weight/fee calculation, producing transactions that underpay the fee rate and may fall below the minimum relay fee — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The reported bug class is a **fee-accounting shortfall**: a collected fee is computed against one cost model while the actual execution carries an additional, unaccounted cost, yielding a deterministically under-funded operation that cannot complete. In `bitcoin-serai`, `SignableTransaction::new` computes `needed_fee` from a synthetic transaction that contains only the payment outputs, while the real transaction also appends an `OP_RETURN` output carrying up to 80 bytes of caller-supplied `data`. That output is never included in the weight/vbytes estimate, so the funded fee is smaller than what the actual transaction requires at the requested rate — mirroring the "fee collected but execution cost ignored" flaw.

### Finding Description
`SignableTransaction::new` builds `tx_outs` by first pushing all payments and then appending an `OP_RETURN` output when `data` is `Some` (send.rs, lines 194–202). The fee is then computed by `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204), which constructs a stand-in `Transaction` containing **only** `payments` — the OP_RETURN output that was just pushed to `tx_outs` is absent. The same omission occurs in the change branch, which re-estimates via `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (lines 225–226), again without the data output.

Consequences:

- `needed_fee = fee_per_vbyte * vbytes` uses a vbytes value up to ~85 vbytes too small (OP_RETURN output = 8-byte amount + script with up to 80 bytes of data plus pushdata overhead, non-discounted).
- The minimum-relay sanity check `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` (line 211) is also evaluated against the underestimated `vbytes`, so it can pass while the *real* transaction's effective feerate is below the relay minimum.
- With no change output, the paid fee equals `needed_fee`, so the actual feerate is strictly below `fee_per_vbyte` and can be below `minRelayTxFee`; the signed transaction is then rejected by `sendrawtransaction`/`testmempoolaccept` or sits unconfirmed indefinitely.
- With change, the change amount is computed as `input_sat - payment_sat - fee_with_change` where `fee_with_change` is also underestimated, so change is *over-credited* relative to the fee that should have been reserved — the change output absorbs value that the fee was supposed to cover, and the transaction still underpays on the wire.

This is the same structural defect as the report: value is allocated under a two-part obligation (payment output weight + data output weight), but only one part is measured, leaving the broadcast/execution stage under-funded.

### Impact Explanation
Any transaction built with `data` (the intended mechanism for attaching Serai `InInstruction`/`Shorthand` payloads to Bitcoin flows, as exercised in `tests/full-stack/src/tests/mint_and_burn.rs`) is produced with an effective feerate lower than requested. When `fee_per_vbyte` is at or near the relay minimum — the common case, since `median_fee` can return `Fee(1)` on an empty block — the transaction's real feerate falls below 1 sat/vbyte and is deterministically rejected or never mined. The result is a denial of service on spends: the `Eventuality` is bound to a txid that will never confirm, locking the plan's inputs, and (in the change case) funds are misattributed to the change output instead of the fee. An unprivileged party triggers this simply by supplying `data` bytes to `SignableTransaction::new`, a public API in the in-scope `networks/bitcoin` wallet crate.

### Likelihood Explanation
The defect is deterministic for every `data`-carrying transaction — no race, threshold collusion, or malicious infrastructure required. Whether it causes outright rejection depends on how close `fee_per_vbyte` is to the minimum; at `Fee(1)` (explicitly permitted by `median_fee`'s `.max(1)` floor, processor/src/networks/bitcoin.rs:414) a ~20%+ size underestimate guarantees a sub-minimum real feerate. At higher feerates it silently degrades priority and inflates change, still mispricing every spend.

### Recommendation
Include the actual `OP_RETURN` `TxOut` (with its real `script_pubkey` length) in the transaction passed to `calculate_weight_vbytes`, for both the no-change and with-change estimates — e.g., pass `&tx_outs`-equivalent output list rather than `payments`, or add the data output to the synthetic transaction before measuring `tx.weight()`. Re-run the minimum-relay check against the final, fully-populated output set, and derive change only after the fee for the *complete* transaction is known.

### Proof of Concept
```rust
// Conceptual: a single-input spend with an 80-byte OP_RETURN at fee_per_vbyte = 1.
let data = vec![0u8; 80];
let tx = SignableTransaction::new(
    vec![output],                 // one P2TR ReceivedOutput
    &[(payment_script, 10_000)],  // one payment
    Some(change_script),
    Some(data.clone()),           // OP_RETURN appended to tx_outs
    1,                            // 1 sat/vbyte
).unwrap();

// needed_fee was computed as vbytes(inputs + 1 payment + change),
// but tx.vsize() also includes the ~85-vbyte OP_RETURN output.
assert!(tx.fee() < tx.tx_ref().vsize() as u64); // real feerate < 1 sat/vbyte
// -> sendrawtransaction rejects with "min relay fee not met" / tx never confirms,
// while change was credited assuming the smaller fee.
```
The divergence is directly observable: `needed_fee()` equals `vbytes(estimated)` while `Transaction::vsize()` of the built `tx` is larger by the size of the OP_RETURN output.