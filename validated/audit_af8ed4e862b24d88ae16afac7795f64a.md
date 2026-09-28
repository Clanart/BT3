The change handling itself explicitly sweeps leftovers (or intentionally burns sub-dust change to fee). The real analog is in the fee/weight accounting: `calculate_weight_vbytes` only models `payments` and `change`, while the OP_RETURN `data` output pushed at send.rs:194-202 is never included in the weight/vbytes estimate.### Title
OP_RETURN `data` output is omitted from transaction weight/vbytes and fee accounting, producing underpriced and potentially non-standard transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes an `OP_RETURN` output carrying up to 80 bytes of caller-supplied `data` onto `tx_outs`, but `calculate_weight_vbytes` — which feeds both the fee computation (`needed_fee = fee_per_vbyte * vbytes`) and the `MAX_STANDARD_TX_WEIGHT` standardness check — builds its model transaction from `payments` and `change` only. The `data` output is never modeled, so every transaction that carries an OP_RETURN payload has its vsize, fee, and weight underestimated, analogous to a swap helper that never accounts for residual assets: the bytes are spent, but never counted.

### Finding Description
`SignableTransaction::new` accepts an optional `data: Option<Vec<u8>>` (up to 80 bytes, enforced at send.rs:171) and appends a `TxOut` with `ScriptBuf::new_op_return(...)` to `tx_outs` at send.rs:194-202. However, both weight computations — the initial call at send.rs:204 and the `weight_with_change` call at send.rs:225-226 — pass only `payments` and `change` to `calculate_weight_vbytes`. Inside that function (send.rs:62-99) the model transaction's `output` vector is built exclusively from `payments` plus an optional `change` output; there is no parameter or code path for the OP_RETURN output.

Consequences:

1. **Underestimated vbytes → underestimated fee.** `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) and `fee_with_change` (send.rs:227) are computed against a vsize that misses the OP_RETURN output (~10 bytes of output overhead + 1–2 pushdata bytes + up to 80 data bytes, all non-witness so counted at full weight — roughly up to ~360 WU / ~90 vbytes). The real transaction is larger than the model, so the *effective* fee rate is strictly below the requested `fee_per_vbyte`.
2. **Min-relay check bypassed.** The `TooLowFee` guard at send.rs:211 validates `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the underestimated `vbytes`. A transaction built at or near the minimum relay rate with a large `data` payload can end up with an actual feerate below the relay minimum, making it unbroadcastable/non-standard — the inputs are locked in a transaction that never confirms (funds paid out as fee/committed but not spendable as intended).
3. **Standardness weight check bypassed.** The `TooLargeTransaction` check at send.rs:241 compares `weight` (without the data output) against `MAX_STANDARD_TX_WEIGHT`. A tx whose real weight exceeds the limit by up to the OP_RETURN size still passes, producing a transaction Bitcoin Core will reject.

Note the sub-dust change burn at send.rs:228-233 is documented intentional behavior and is not the finding; the defect is the accounting omission, not the dust policy.

### Impact Explanation
The multisig can be made to sign and broadcast transactions whose actual feerate is below what was requested — including below the network minimum relay fee — or whose real weight exceeds `MAX_STANDARD_TX_WEIGHT`. Such transactions will not propagate or confirm, stranding the consumed UTXOs in limbo (the protocol believes a payment was made; the network will never mine it without re-signing). This matches the reported class: assets committed by a contract (here, satoshis committed as inputs and as fee) with no accounting of the remainder — the uncounted output silently corrupts the economic parameters of the signed transaction.

### Likelihood Explanation
Reachable whenever `SignableTransaction::new` is invoked with a `data` payload — the processor uses this path for Bitcoin outputs carrying out-instruction/refund metadata (`processor/src/networks/bitcoin.rs` calls into this wallet code). The trigger requires no privileged access beyond influencing a withdrawal that includes OP_RETURN data, and the bug is deterministic — the data output is *always* omitted from the model. The severity is gated by how close `fee_per_vbyte` is to the relay minimum and how large `data` is; with a maximal 80-byte payload and a fee rate near the floor, the transaction is guaranteed non-standard/underpriced. Medium: deterministic accounting error with a plausible path to unspendable/unbroadcastable outputs, but no direct theft.

### Recommendation
Include the `data` output in the weight model: either pass the constructed `OP_RETURN` `ScriptBuf` into `calculate_weight_vbytes` as an additional output, or compute the weight on `tx_outs` *after* the OP_RETURN push rather than on `payments` alone. Alternatively, perform a final assertion that the assembled `tx`'s actual `weight()`/`vsize()` is within the estimate, and recompute `needed_fee` and the `MAX_STANDARD_TX_WEIGHT` check against the real transaction.

### Proof of Concept
```rust
// In a test against SignableTransaction::new (networks/bitcoin/src/wallet/send.rs):
let inputs = vec![ /* one ReceivedOutput with sufficient value */ ];
let payments = vec![(p2tr_script_buf(some_key), 10_000)];
let data = vec![0xAA; 80]; // max legal payload

let tx = SignableTransaction::new(inputs, &payments, None, Some(data), 1 /* sat/vB */).unwrap();

// needed_fee was computed on vbytes EXCLUDING the ~90-vbyte OP_RETURN output:
let real_vbytes = tx.transaction().vsize() as u64; // larger than the modeled vsize
let real_feerate = tx.fee() as f64 / real_vbytes as f64;
assert!(real_feerate < 1.0); // below DEFAULT_MIN_RELAY_TX_FEE -> unbroadcastable
```
The fee check at send.rs:211 passes because `vbytes` excludes the data output, while the signed transaction's true size makes its effective feerate sub-minimal — miners will never include it and the spent inputs are stranded in an unconfirmable transaction.