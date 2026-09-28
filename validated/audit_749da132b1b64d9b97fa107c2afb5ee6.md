### Title
`SignableTransaction::new` computes fee, weight and the `MAX_STANDARD_TX_WEIGHT` check without the OP_RETURN output, producing an underpriced or over-weight transaction that cannot be broadcast - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external bug is "resource/size accounting that diverges from reality makes a legitimately-constructed message unprocessable, locking the user's funds". The Serai analog is in `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`): the OP_RETURN data output is pushed onto `tx_outs` at line 195, but both `calculate_weight_vbytes` calls (lines 204 and 226) are made with `payments` only, which never includes the OP_RETURN output. The estimated `weight` and `vbytes` therefore understate the real transaction, so `needed_fee` underpays the target fee rate and the `MAX_STANDARD_TX_WEIGHT` check at line 241 can pass for a transaction that is actually non-standard.

### Finding Description
- `tx_outs.push(TxOut { value: ZERO, script_pubkey: new_op_return(data) })` executes before any size estimation (send.rs:194-202).
- `calculate_weight_vbytes(tx_ins.len(), payments, None)` builds a mock transaction from `payments` only; the data output is absent, so `weight`/`vbytes` are too small (send.rs:204, 62-101).
- `needed_fee = fee_per_vbyte * vbytes` is computed from the understated vsize (send.rs:206), and the minimum-relay-fee guard uses the same understated `vbytes` (send.rs:211).
- The change path repeats the same omission: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (send.rs:225-226). `weight`/`needed_fee` are updated to `weight_with_change`/`fee_with_change`, which still exclude the OP_RETURN output.
- Finally `if weight > MAX_STANDARD_TX_WEIGHT` (send.rs:241) validates a weight that excludes the OP_RETURN output, so a transaction whose real weight exceeds 400,000 WU is accepted, signed via `multisig()`/`TransactionMachine`, and is then unbroadcastable — Bitcoin nodes will reject it as non-standard. Symmetrically, even when under the weight cap, the transaction pays `needed_fee` over a vsize that is smaller than reality, so the effective feerate can fall below the sender's intent (or below the 1 sat/vB minimum relay rate at the boundary), making the signed transaction unrelayable.
- Once signed, the transaction is committed (the scheduler marks payments as sent); an unbroadcastable signed transaction means the expected outputs never materialize, which in the processor surfaces as `panic!("created a too large transaction despite limiting inputs/outputs")` or a permanently unconfirmed spend — funds stuck rather than delivered.

### Impact Explanation
A caller supplies `data` (up to 80 bytes per the `TooMuchData` check) alongside payments that push the transaction near the standardness/weight boundary, or near the minimum relay feerate. The resulting transaction either (a) exceeds `MAX_STANDARD_TX_WEIGHT` while passing the check, or (b) pays a fee below what the requested `fee_per_vbyte` implies, potentially below relay minimums. In both cases the transaction is signed by the threshold but cannot confirm — the exact "message constructed per the API cannot be finalized, funds locked" shape of the source bug. In the processor context this escalates to a panic/`NetworkError` path since `make_signable_transaction` treats `TooLargeTransaction` and `TooLowFee` as unreachable.

### Likelihood Explanation
Reachable by any caller of the public wallet API that attaches `data`; the miscalculation is deterministic, not probabilistic. Triggering the boundary condition requires a transaction near the weight limit or at minimum feerate, which requires many inputs/outputs — feasible since the scheduler batches up to `MAX_INPUTS`/`MAX_OUTPUTS` of 520. Severity is Medium: the omission is bounded (~8-90 vbytes per OP_RETURN), so it only matters at the margin, but the consequence (unbroadcastable signed transaction / locked payment intent) is concrete.

### Recommendation
Include the OP_RETURN output in the size estimation: build the mock transaction inside `calculate_weight_vbytes` from the final output set (payments + optional data output + optional change), or add a conservative bound for the data output's weight to `weight`/`vbytes` before computing `needed_fee` and before the `MAX_STANDARD_TX_WEIGHT` check.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// tx_outs gets the OP_RETURN output pushed at line 195 ...
// ... but the weight/vsize used for needed_fee and the size check
// only contain `payments` (and optionally `change`):
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
...
if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
    Err(TransactionError::TooLargeTransaction)?;
}
// The returned SignableTransaction.tx includes the OP_RETURN output,
// so its real weight = `weight` + weight_of(op_return_output),
// and real vsize > vbytes -> effective feerate < fee_per_vbyte.
```
A caller passing `data` of 80 bytes plus payments totaling real weight within ~100 WU of 400,000 produces a `SignableTransaction` that passes `new()` yet exceeds `MAX_STANDARD_TX_WEIGHT` once the OP_RETURN output is serialized — the signed transaction is rejected by every relaying node.