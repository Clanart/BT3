### Title
`SignableTransaction::new` omits the OP_RETURN data output from the fee/weight calculation, underpaying fees (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes code referencing a symbol/output that doesn't exist at compile time — a "missing element" bug class. The analog in Serai is a transaction whose real output (the OP_RETURN `data` output pushed into `tx_outs`) is missing from the weight/vbytes calculation used to derive `needed_fee`. The transaction actually signed contains an output the fee formula never accounted for, so every transaction carrying `data` underpays relative to the caller-specified `fee_per_vbyte`.

### Finding Description
`SignableTransaction::new` appends an OP_RETURN output of up to 80 bytes (plus ~9 bytes of script overhead) to `tx_outs` at networks/bitcoin/src/wallet/send.rs:194-202. The weight and vbytes used for fee estimation, however, are computed by `calculate_weight_vbytes` at line 204 (and again at lines 225-226 for the change case) using only `tx_ins.len()`, `payments`, and the change script — the `data` output is never passed in. Consequently:

- `needed_fee = fee_per_vbyte * vbytes` at lines 206/227 is computed for a transaction that is ~95+ bytes (~380+ weight units) smaller than the one actually produced.
- The change amount at line 228 (`input_sat - payment_sat - fee_with_change`) is overpaid to change by exactly the missing output's fee contribution, leaving the actual fee at `fee_with_change` — too low for the real, larger transaction.
- The `TooLowFee` check at line 211 validates the underestimated fee against an underestimated vbytes, so a caller asking for exactly the minimum relay feerate produces a transaction whose *real* feerate is below `DEFAULT_MIN_RELAY_TX_FEE` and will be rejected by standard Bitcoin relay.

Any caller (including the coordinator path in `processor/src/networks/bitcoin.rs` `make_signable_transaction`, and the mint/burn flow which uses OP_RETURN `Shorthand` payloads) that passes `Some(data)` produces a transaction paying less than the requested rate.

### Impact Explanation
An attacker or ordinary user can trigger construction of a transaction that is signed by the FROST multisig yet fails relay acceptance or is economically under-fee'd relative to the intended rate. The signatures commit via `Prevouts::All` to the real transaction (including the OP_RETURN), so the signed shares cannot be redirected to a corrected transaction — the signed, broadcastable artifact is fixed to the under-fee'd form. For time-sensitive protocol flows, a signed transaction that nodes reject as `min relay fee not met` stalls the spend of scanned outputs; with change, the excess is silently diverted into the change output rather than the fee. Impact is a concrete fee/validity miscalculation reachable purely via the public `data` parameter — Medium.

### Likelihood Explanation
The error is deterministic: any `SignableTransaction::new` call with `data` of length `L` underpays by `~fee_per_vbyte * ceil((L + ~10) weight-adjusted bytes)`. Whether it crosses a relay threshold depends on `fee_per_vbyte`, but the fee is always wrong when data is present, and minimum-feerate transactions are a normal configuration.

### Recommendation
Include the OP_RETURN output when estimating weight: build the output vector inside `calculate_weight_vbytes` from the same `tx_outs` the transaction will use (payments + optional data output + optional change), or pass `data` into it and push the `TxOut` there, so `needed_fee`, `fee_with_change`, the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the final transaction.

### Proof of Concept
```rust
// networks/bitcoin context; key/scanner setup as in tests/wallet.rs
let inputs = vec![received_output]; // one scanned ReceivedOutput
let payments = vec![(addr(), 1000)];
let data = vec![0u8; 80]; // max allowed

let tx = SignableTransaction::new(inputs, &payments, Some(change_addr), Some(data), 1)
  .unwrap();

// needed_fee was computed without the ~85-byte OP_RETURN output:
assert!(tx.transaction().output.iter().any(|o| o.script_pubkey.is_op_return()));
// Real vsize exceeds the vbytes used for needed_fee:
let real_vsize = u64::try_from(tx.transaction().vsize()).unwrap();
// fee paid = tx.fee(); effective feerate = tx.fee() / real_vsize < 1 sat/vb
// when change is present (change absorbs input - payments - underestimated_fee),
// so the signed tx is below DEFAULT_MIN_RELAY_TX_FEE and is rejected by relay.
```