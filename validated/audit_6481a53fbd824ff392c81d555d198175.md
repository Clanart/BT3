### Title
`SignableTransaction::new` omits the OP_RETURN `data` output from the weight/vbytes calculation, so `needed_fee` is not updated for the added output and the transaction underpays its fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` pushes an OP_RETURN output onto `tx_outs` when `data` is `Some`, but the weight/vbytes estimate used to compute `needed_fee` and to enforce the minimum relay fee is built only from `payments` (and optionally `change`). The `data` output is never included in the weight calculation, so the fee variable is stale relative to the actual transaction that gets signed — the same bug class as the YoloV2 finding where one path mutates state without updating the corresponding accounting variable.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is appended to `tx_outs` before the weight calculation:

```rust
// networks/bitcoin/src/wallet/send.rs
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` reconstructs a `Transaction` from `payments` only:

```rust
output: payments.iter().map(|payment| TxOut { ... }).collect(),
```

It takes `payments` and `change` as parameters — there is no `data` parameter, so the OP_RETURN output is structurally incapable of being counted. Consequences:

1. `vbytes` underestimates the real transaction size by roughly `8 (value+len) + 1 + pushdata_len` vbytes — up to ~90 vbytes for the maximum 80-byte payload.
2. `needed_fee = fee_per_vbyte * vbytes` is therefore too low; the actual fee paid (`sum(inputs) - sum(outputs)`, per `fee()`) is `needed_fee` only when no change is created, so the realized fee rate is below `fee_per_vbyte`.
3. The `TooLowFee` guard (`needed_fee < DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`) is evaluated against the underestimated `vbytes`, so a transaction can pass the check while paying below the minimum relay fee once the OP_RETURN output is included.
4. The `MAX_STANDARD_TX_WEIGHT` check uses `weight`, which also excludes the OP_RETURN output, so an oversized transaction is not rejected.

Note the contrast: when a `change` output is added later, the code correctly recomputes `weight` and `needed_fee` (`weight = weight_with_change; needed_fee = fee_with_change;`). The `data` path performs no such update — exactly the asymmetric-state-update defect in the reference report.

### Impact Explanation
Any caller that constructs a transaction with `data` set produces a signed transaction whose fee is up to ~`fee_per_vbyte * 90` sat lower than intended, and possibly below the network's minimum relay fee. Such a transaction will be rejected/dropped by relaying nodes, leaving the (threshold-signed) UTXOs unable to move until a replacement transaction is produced — funds effectively stuck, and the published `needed_fee()`/`fee()` accounting is inconsistent with the signer's intent. In the Serai processor context, `data` carries arbitrary instruction payloads that external users can cause to be embedded, so the buggy path is reachable from public inputs.

### Likelihood Explanation
Triggered whenever `SignableTransaction::new` is called with `Some(data)` — a routine path for transactions that embed instruction data. The miscalculation is deterministic; whether it causes relay failure depends on the margin between the requested fee rate and the relay minimum, but the fee is always underpaid by the size of the data output.

### Recommendation
Include the OP_RETURN output in the size estimate. Either pass the data payload (or the fully-built `tx_outs`) into `calculate_weight_vbytes` so the dummy `Transaction` mirrors the real one, or add the output's serialized size to the weight afterward. Then re-derive `vbytes`, `needed_fee`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check from the corrected weight — mirroring how the change path recomputes `weight_with_change`/`fee_with_change`.

### Proof of Concept
```rust
// Conceptual: construct a tx with an 80-byte OP_RETURN payload
let data = Some(vec![0xaa; 80]);
let tx_with_data = SignableTransaction::new(inputs.clone(), payments, None, data, FEE).unwrap();
let tx_no_data   = SignableTransaction::new(inputs, payments, None, None, FEE).unwrap();

// needed_fee is identical even though tx_with_data is ~90 vbytes larger
assert_eq!(tx_with_data.needed_fee(), tx_no_data.needed_fee());

// The real fee rate is lower than FEE
let real_vbytes = tx_with_data.transaction().vsize() as u64;
assert!(tx_with_data.needed_fee() < FEE * real_vbytes);
```