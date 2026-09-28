### Title
Fee and minimum-relay checks omit the OP_RETURN output's weight, producing under-priced transactions that can never confirm - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes the `data` (OP_RETURN) output into `tx_outs` before computing the transaction weight, but calls `calculate_weight_vbytes` with `payments` — not `tx_outs`. Both the no-change and with-change fee estimates therefore exclude the OP_RETURN output entirely. The resulting transaction pays `needed_fee` over a larger real vsize, so its true feerate is lower than the caller-requested `fee_per_vbyte`, and it can fall below the minimum relay feerate while still passing the `TooLowFee` check.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`):

1. The OP_RETURN output is appended to `tx_outs` at lines 193–202.
2. Weight/vbytes are computed at line 204 via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — using `payments`, which does not contain the data output.
3. `calculate_weight_vbytes` builds the dummy transaction's outputs only from `payments` plus an optional `change` (lines 85–99), so the OP_RETURN's ~9–90 bytes are never counted.
4. `needed_fee = fee_per_vbyte * vbytes` (line 206) and the minimum-relay check `(DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` (line 211) both use this understated `vbytes`.
5. When `change` is provided, the change amount is computed as `input_sat - (payment_sat + fee_with_change)` (line 228), where `fee_with_change` is likewise computed without the data output (lines 225–227). So the *actual* fee paid — `sum(inputs) - sum(outputs)`, see `fee()` at lines 138–141 — equals exactly `needed_fee`, which was priced for a smaller transaction.

Net effect: the real feerate is `needed_fee / (vbytes + data_vbytes) < fee_per_vbyte`. A 80-byte OP_RETURN (maximum allowed, line 171) adds roughly 89 vbytes; for typical single-input/single-output transactions (~140–200 vbytes) this can halve the effective feerate or push it below 1 sat/vB.

### Impact Explanation
The transaction is produced, passed to `multisig`/`TransactionMachine`, and signed under `Prevouts::All` with `taproot_key_spend_signature_hash` (send.rs lines 373–397). Once signed and broadcast, a transaction priced under the relay minimum is rejected by the network mempool: it never confirms, yet the inputs' outpoints are consumed as far as the creator (and any `txid()`-based eventuality tracking, send.rs lines 259–263) is concerned. The affected outputs are effectively locked — funds that cannot be spent via this transaction and cannot be re-spent without rebuilding and re-signing a new transaction. Even when it does relay, users paying `fee_per_vbyte` get materially less than they specified. This is a concrete fee-handling bug, not a best-practice issue: the fee charged and the relay check are computed against a transaction shape that is not the one being signed.

### Likelihood Explanation
Triggering requires only calling `SignableTransaction::new` with `data: Some(...)` — a fully public, documented parameter ("If data is specified, an OP_RETURN output will be added", line 149). No secret or trusted party is involved; the caller supplies ordinary transaction data. Whether the underpriced transaction is actually non-standard depends on how much slack `fee_per_vbyte` had above the relay minimum, but the feerate is always lower than requested.

### Recommendation
Compute the weight over the outputs actually being signed. Change the `calculate_weight_vbytes` calls to pass `&tx_outs` (after the OP_RETURN push) rather than `payments`, i.e. at line 204 use the already-built `tx_outs`, and at lines 225–227 compute the with-change weight over `tx_outs` plus the change script. Alternatively, include the data output as an explicit parameter to `calculate_weight_vbytes`. This makes `needed_fee`, the `TooLowFee` check, and the change computation all reflect the final transaction.

### Proof of Concept
```rust
// Conceptual: construct a 1-in/1-out TX with an 80-byte OP_RETURN.
let inputs = vec![received_output];                       // a spendable ReceivedOutput
let payments = vec![(p2tr_script_buf(key).unwrap(), 546)]; // dust-limit payment
let data = Some(vec![0u8; 80]);                            // passes the <= 80 check (line 171)

let tx = SignableTransaction::new(inputs, &payments, None, data, fee_per_vbyte).unwrap();

// tx.needed_fee() == fee_per_vbyte * vbytes, where vbytes was computed WITHOUT the
// OP_RETURN output (line 204 uses `payments`, not `tx_outs`).
// The real transaction contains an extra ~89-vbyte output, so
// tx.fee() / tx.transaction().vsize() < fee_per_vbyte,
// and can be < 1 sat/vB even though the TooLowFee check at line 211 passed.
// The signed TX is rejected by Bitcoin Core relay, leaving its inputs effectively locked.
```

Relevant code: OP_RETURN pushed to `tx_outs` (`send.rs` lines 193–202), weight computed from `payments` (`send.rs` lines 85–99, 204), fee and relay check (`send.rs` lines 206–213), change priced the same way (`send.rs` lines 224–234).