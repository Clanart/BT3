### Title
`SignableTransaction::new` excludes the OP_RETURN output from weight/fee estimation, producing transactions that underpay the fee and oversized change — funds can be locked in an unrelayable transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes transaction weight, vbytes, `needed_fee`, and the change amount using only the `payments` slice, after it has already appended an OP_RETURN data output to `tx_outs`. The OP_RETURN output (up to ~91 bytes when `data` is the permitted 80-byte maximum) is never counted in the fee estimate. The resulting signed transaction is larger than estimated, so its effective feerate is below `fee_per_vbyte` — and the `TooLowFee` check passes against the under-estimated vbytes. At low fee rates this yields a transaction below the minimum relay fee that cannot be broadcast or confirmed, trapping all input funds and any credited change — the same class as the external report's rewards stuck in the contract.

### Finding Description
In `send.rs`, the payment outputs are collected into `tx_outs`, then the OP_RETURN output is pushed into `tx_outs` (lines 188–201). However, both weight calculations pass `payments`, not `tx_outs`:

- `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204
- `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at line 226

`calculate_weight_vbytes` (lines 62–99) builds a transaction containing exactly the slice it is given plus the optional change output, so the OP_RETURN output is omitted from the weight. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) is under-computed.
2. The `TooLowFee` check at line 211 compares against `vbytes` that excludes the OP_RETURN output, so a transaction that is actually below `DEFAULT_MIN_RELAY_TX_FEE` passes validation.
3. The change value `input_sat - (payment_sat + fee_with_change)` (line 228) is computed with an under-estimated `fee_with_change`, so the change output is over-valued by the same amount and the transaction actually pays only `needed_fee` against a larger true size — an effective feerate strictly less than `fee_per_vbyte`.

With `fee_per_vbyte` at or near 1 sat/vB and a large `data` payload (e.g., 80 bytes → ~90 extra vbytes ≈ 8–15% size increase on a typical transaction), the real feerate can fall below the 1 sat/vB minimum relay policy. The transaction will be rejected by `send_raw_transaction` / never relayed, and since it was already signed with `SIGHASH_ALL`-style commitments binding all inputs and outputs, those UTXOs are spent by a transaction that cannot enter the mempool — the funds are as trapped as the undistributed reward tokens in the external report, and any "refund" requires reconstructing and re-signing an entirely new transaction.

### Impact Explanation
All funds referenced by the transaction's inputs (multisig-controlled UTXOs, including any user deposits being forwarded/refunded) plus the over-credited change output are locked into a transaction that underpays the fee relative to what the caller requested. If the true feerate falls below the relay minimum, the transaction cannot be broadcast; if it is merely low, the funds are unconfirmable until fee market conditions drop — in both cases liquidity is stuck and the change/payment values committed in the signature cannot be adjusted without a new signing round. An unprivileged caller can reach this path through any integration that lets them supply `data` (the API explicitly supports it, tested with up to 80 bytes) while requesting a near-minimum fee rate.

### Likelihood Explanation
Requires the caller to specify `data` (OP_RETURN) together with a `fee_per_vbyte` such that dropping the OP_RETURN weight pushes the effective feerate below relay policy or below what is economically viable. The fee miscalculation itself occurs on every `SignableTransaction::new` call with `data.is_some()` — it is deterministic, not edge-dependent. The trapped-funds outcome additionally needs a low requested fee rate or a congested mempool, making the full impact conditional; hence Medium rather than High. Note the in-repo processor (`processor/src/networks/bitcoin.rs:446-452`) always passes `None` for `data`, so exploitation requires a caller/integration that uses the `data` parameter of the public API.

### Recommendation
In `SignableTransaction::new`, pass the fully constructed `tx_outs` (payment outputs plus the OP_RETURN output) to `calculate_weight_vbytes` rather than `payments`. Since `calculate_weight_vbytes` takes `&[(ScriptBuf, u64)]`, either build the OP_RETURN script before the weight calls and include it in the slice, or change `calculate_weight_vbytes` to accept `&[TxOut]`/`tx_outs` directly so weight is always computed over the exact output set that will be signed.

### Proof of Concept
```rust
// networks/bitcoin conceptual PoC against SignableTransaction::new
let output = send_and_get_output(&rpc, &scanner, key).await; // e.g. 20_000 sats
let inputs = vec![output];
let payments = vec![(p2tr_script_buf(key).unwrap(), 10_000)];
let data = Some(vec![0u8; 80]); // maximum allowed OP_RETURN payload

let stx = SignableTransaction::new(inputs, &payments, None, data, 1 /* sat/vB */).unwrap();
// stx.needed_fee() is computed from a weight that omits the ~90-byte OP_RETURN output.
// After signing, tx.vsize() > needed_fee, so fee = needed_fee gives < 1 sat/vB effective rate.
// The TooLowFee check (line 211) already passed against the under-estimated vbytes.
// rpc.send_raw_transaction(&tx) -> rejected: "min relay fee not met"
// -> the input UTXO's 20_000 sats are bound to a signature for a transaction
//    that cannot be relayed or confirmed.
```

Root cause lines: `tx_outs` gains the OP_RETURN output at `send.rs:194-201`, but weight is computed from `payments` at `send.rs:204` and `send.rs:226`, and `calculate_weight_vbytes` (`send.rs:62-99`) builds its weight-estimation transaction solely from the slice it is given.