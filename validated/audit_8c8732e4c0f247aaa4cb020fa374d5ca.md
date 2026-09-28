### Title
`SignableTransaction::new` omits the OP_RETURN output from the fee/weight calculation, producing transactions that underpay the requested fee rate and may fall below the minimum relay fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an OP_RETURN output carrying up to 80 bytes of caller-supplied `data` to `tx_outs`, but computes the transaction weight/vbytes — and therefore `needed_fee` — solely from `payments` (and optionally `change`). The analog to the fee-on-transfer report: the code accounts for a smaller "actual" size than what is really committed, so the funds reserved for the fee are insufficient for the transaction actually produced.

### Finding Description
The OP_RETURN output is pushed to `tx_outs` before the weight is calculated:

- `tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })` at send.rs:194-202
- `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at send.rs:204 only serializes `payments` (and later `change` at send.rs:225-226); the OP_RETURN `TxOut` is never included in either the `weight`/`vbytes` used for `needed_fee` nor in the `MAX_STANDARD_TX_WEIGHT` check.

Consequences:

1. `needed_fee = fee_per_vbyte * vbytes` (send.rs:206) is computed on a transaction that is smaller than the real one by the full OP_RETURN output size (~10 + data_len bytes, up to ~90 bytes). The transaction that gets signed via `taproot_key_spend_signature_hash` (send.rs:386) and broadcast is the larger one, so its effective fee rate is strictly below `fee_per_vbyte`.
2. The `TooLowFee` guard (send.rs:211) checks `needed_fee >= DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` against the underestimated `vbytes`. For a small transaction (e.g., 1 input, 1 payment, ~150 vB estimated) with an 80-byte OP_RETURN, the real vsize can be ~60% larger, pushing the effective rate well under 1 sat/vB — below the default minimum relay fee — so nodes will reject the transaction even though `needed_fee` "passed" the check.
3. The `TooLargeTransaction` check (send.rs:241) uses the same underestimated `weight`, so a transaction that actually exceeds `MAX_STANDARD_TX_WEIGHT` can be constructed.

### Impact Explanation
A `SignableTransaction` produced with `data` set commits signatures (via `Prevouts::All` and the Taproot sighash) to a transaction whose fee rate is lower than requested and potentially below relay minimums. The resulting transaction may fail to propagate/confirm, stalling the multisig's plan completion and effectively freezing the spent outputs until a replacement is constructed — i.e., funds presumed moved are not actually spendable as signed. This mirrors the report's "received less than accounted → subsequent step fails" shape: the fee budget accounted is smaller than what the real transaction requires.

### Likelihood Explanation
Reachable whenever `SignableTransaction::new` is invoked with `Some(data)` — i.e., any plan/payment flow that attaches OP_RETURN data, which in Serai is driven by user-supplied instruction data. The underpayment is deterministic (proportional to `data.len()`); hitting the sub-minimum-relay case requires a small estimated vsize and a large `data` payload with a low `fee_per_vbyte`, which is realistic at 1 sat/vB feerates. No malicious validator or collusion required — only an unprivileged party supplying transaction data.

### Recommendation
Include the OP_RETURN output in the weight/vbytes estimation. Construct the full output list (payments + OP_RETURN + optional change) before calling `calculate_weight_vbytes`, or add an explicit `data_len` parameter so the dummy `Transaction` includes a `TxOut { value: ZERO, script_pubkey: <op_return of correct length> }`. Re-check `TooLowFee` and `MAX_STANDARD_TX_WEIGHT` against the final output set.

### Proof of Concept
Conceptual trace over `networks/bitcoin/src/wallet/send.rs`:

```rust
let payments = [(p2tr_script_buf(key).unwrap(), 10_000u64)];
let data = Some(vec![0xaa; 80]); // max allowed OP_RETURN payload
let tx = SignableTransaction::new(vec![output], &payments, None, data, 1).unwrap();
```

- `tx_outs` ends with `payment + OP_RETURN` (send.rs:188-202): 2 outputs.
- `calculate_weight_vbytes(1, &payments, None)` (send.rs:204) builds a dummy tx with **only the payment output**, yielding vbytes ≈ 150.
- `needed_fee = 1 * 150`, and `150 ≥ 1000*150/1000` passes `TooLowFee` (send.rs:206-213).
- The real transaction (send.rs:245-251) contains the extra ~90-byte OP_RETURN output, so its actual vsize ≈ 240 while only 150 sats of fee are paid → effective ≈0.63 sat/vB < `DEFAULT_MIN_RELAY_TX_FEE`.
- `sign()` then commits all signatures to this under-fee'd transaction via `taproot_key_spend_signature_hash` with `Prevouts::All` (send.rs:373-390); the completed `Transaction` is rejected by standard relay policy, and the consumed `ReceivedOutput`s remain locked in a plan that can never complete as signed.