### Title
`SignableTransaction::new` omits the OP_RETURN data output from the fee/weight calculation, producing transactions that pay less than the requested fee rate (and potentially below the minimum relay fee) - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
When a `SignableTransaction` is constructed with `data`, an `OP_RETURN` output carrying up to 80 bytes is pushed into `tx_outs`. However, both calls to `calculate_weight_vbytes` only receive `payments` (and optionally `change`), never the OP_RETURN output. The virtual size of the final transaction is therefore underestimated by roughly 90+ weight units, so `needed_fee` is lower than the fee actually required for the specified `fee_per_vbyte`.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150`), the transaction outputs are built from `payments`, then an `OP_RETURN` output is appended when `data` is present (lines 193-202). The weight/vsize calculation at lines 204 and 225-226 reconstructs a model transaction from `tx_ins.len()`, `payments`, and `change` only — the `data` output is not modeled:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
...
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
```

`calculate_weight_vbytes` (lines 62-127) builds `tx.output` exclusively from `payments` plus the optional change output, so a ~92-byte OP_RETURN output (`8` value bytes + `~84` script bytes for 80 bytes of data) is excluded. Both the `TooLowFee` check (line 211) and the change-dust decision (line 228) are computed against this underestimated vsize.

### Impact Explanation
- The signed transaction's effective fee rate is lower than the caller-specified `fee_per_vbyte`; `fee() - needed_fee()` is not conserved.
- The `TooLowFee` guard is meant to guarantee the transaction clears `DEFAULT_MIN_RELAY_TX_FEE` for its true size. Because the OP_RETURN output isn't counted, a transaction carrying data can pass this check while its real feerate falls below the minimum relay fee, causing it to be rejected by relaying nodes — the spend (e.g., a withdrawal/burn carrying Serai data) silently fails to propagate and the consumed inputs are tied up in a transaction that never confirms.
- Separately, the change-vs-fee decision uses the wrong size: when change is barely over `DUST` it can be attached to a transaction that is actually underpriced, and when change is dropped the entire leftover becomes fee without the caller being told the true rate.

This is reachable purely from public inputs: an external user initiates a Bitcoin send with attached `data` (the documented `data: Option<Vec<u8>>` path, used for Serai `InInstruction`/`OutInstruction` flows), and the library produces a malformed-fee transaction that the threshold group then signs.

### Likelihood Explanation
Deterministic. Every `SignableTransaction::new` call with non-`None` `data` under-counts the transaction vsize by the full serialized size of the OP_RETURN output (~13 vbytes minimum, up to ~93 vbytes for 80 bytes of data). Whether the resulting transaction falls below min-relay depends on how close `fee_per_vbyte` is to the minimum, but the fee-rate undershoot is unconditional. `test_data` in `networks/bitcoin/tests/wallet.rs` exercises the data path yet never asserts on fee, so the discrepancy is uncaught.

### Recommendation
Include the OP_RETURN output in the modeled transaction. Either pass the fully built `tx_outs` into `calculate_weight_vbytes` (i.e., compute weight after constructing all outputs), or add a `data: Option<&[u8]>` parameter that pushes the same `ScriptBuf::new_op_return` output into the model transaction before measuring `tx.weight()`. Apply this to both the no-change and with-change calculations so `needed_fee`, `TooLowFee`, and the dust check all reflect the final transaction.

### Proof of Concept
```rust
// Conceptual PoC against networks/bitcoin/src/wallet/send.rs
let data = vec![0u8; 80]; // max allowed by the TooMuchData check
let tx = SignableTransaction::new(
    vec![output],        // a ReceivedOutput from Scanner::scan_transaction
    &payments,
    Some(change_script),
    Some(data.clone()),
    fee_per_vbyte,
).unwrap();

// The real serialized size includes the OP_RETURN output:
let actual_vbytes = tx.transaction().vsize() as u64; // includes OP_RETURN output
// needed_fee was computed WITHOUT the OP_RETURN output:
assert!(tx.needed_fee() < fee_per_vbyte * actual_vbytes);
// Consequently fee() (inputs - outputs) corresponds to a lower real feerate
// than `fee_per_vbyte`, and may be below DEFAULT_MIN_RELAY_TX_FEE for the
// true size even though the TooLowFee check passed.
```
Root cause is at `networks/bitcoin/src/wallet/send.rs:204` and `send.rs:225-226`: `calculate_weight_vbytes` is invoked with `payments` only, while the OP_RETURN output appended at `send.rs:194-201` is present in the final `tx.output` but absent from the size model.