### Title
SignableTransaction omits the OP_RETURN data output from weight/fee calculation, producing transactions that can fall below the minimum relay fee — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analog of "missing `payable` prevents `execute` from transferring value": `SignableTransaction::new` appends an `OP_RETURN` output to `tx_outs` when `data` is provided, but both fee calculations call `calculate_weight_vbytes(tx_ins.len(), payments, ...)` which rebuilds a transaction containing only `payments` (and optional change) — never the data output. The estimated `vbytes`/`weight` are therefore too low, `needed_fee` is undercharged, and the resulting signed transaction's effective fee rate is lower than `fee_per_vbyte` and can fall below `DEFAULT_MIN_RELAY_TX_FEE`, making it non-relayable — the analogue of `execute` reverting when `_value != 0`: a signed transaction that cannot actually move funds.

### Finding Description
`SignableTransaction::new` pushes an `OP_RETURN` output (up to 80 bytes of data plus overhead) into `tx_outs` before the fee is computed (send.rs:194-202). The fee computation then calls `calculate_weight_vbytes(tx_ins.len(), payments, None)` and `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (send.rs:204, 225-227), and `calculate_weight_vbytes` constructs its measurement transaction solely from `payments` and `change` (send.rs:85-99) — the OP_RETURN output is never included. `needed_fee = fee_per_vbyte * vbytes` and `fee_with_change = fee_per_vbyte * vbytes_with_change` are both computed against the undersized transaction, and the too-low-fee guard `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` (send.rs:211) also uses the underestimated `vbytes`.

The final signed `Transaction` does include the OP_RETURN output (send.rs:246-251), so its real vsize is larger than what was paid for. The actual fee paid is `input_sat - payment_sat - change` which equals the computed `needed_fee`/`fee_with_change` — a satoshi amount sized for a smaller transaction.

### Impact Explanation
Any transaction carrying a `data` payload (reachable via an OutInstruction with `data`, or the processor's Serai-data path consumed by `extract_serai_data`) is signed with a fee computed for a smaller transaction. Consequences:

- The effective sat/vbyte rate is strictly below the requested `fee_per_vbyte`, breaking the scheduler's fee assumptions.
- When `fee_per_vbyte` is at or near the minimum relay rate, the real fee rate can fall below `DEFAULT_MIN_RELAY_TX_FEE`. The `TooLowFee` check at send.rs:211 uses the underestimated `vbytes`, so it fails to catch this; the signed transaction is then rejected by Bitcoin mempool policy — "funds reported received that are not spendable" / an execution that cannot happen despite valid signatures, directly mirroring the report's "execute can revert when `_value != 0`".
- If a change output exists, the change amount is inflated by exactly the underpaid fee, so the miscalculation is silent (the TX still balances) until broadcast fails or confirms slowly.

### Likelihood Explanation
Every `SignableTransaction` created with `data: Some(_)` undercounts its size by the full serialized size of the OP_RETURN output (~9 + script bytes, roughly 90+ WU for 80 bytes of data ≈ 22+ vbytes plus output overhead). The `data` argument is set from transaction-originated instructions, so an unprivileged party supplying instruction `data` reaches this path. Whether the transaction becomes unrelayable depends on how close `fee_per_vbyte` is to the minimum; the fee-rate distortion is unconditional.

### Recommendation
Include the data output in the fee/weight estimation — e.g., build `tx_outs` first (payments + OP_RETURN + tentative change) and pass the complete output list, or add the OP_RETURN `TxOut` into the transaction constructed in `calculate_weight_vbytes` when `data.is_some()`. Then re-run the `TooLowFee` check against the true vsize.

### Proof of Concept
```rust
// Construct two SignableTransactions identical except for `data`.
let outputs = vec![received_output]; // inputs worth >> payments
let payments = [(p2tr_script_buf(key).unwrap(), DUST)];

let tx_no_data = SignableTransaction::new(
    outputs.clone(), &payments, None, None, fee_per_vbyte).unwrap();
let tx_data = SignableTransaction::new(
    outputs, &payments, None, Some(vec![0u8; 80]), fee_per_vbyte).unwrap();

// Both report the same needed_fee...
assert_eq!(tx_no_data.needed_fee(), tx_data.needed_fee());
// ...yet the data transaction is strictly larger,
// so its actual fee rate is below fee_per_vbyte.
assert!(tx_data.transaction().vsize() > tx_no_data.transaction().vsize());
// With fee_per_vbyte == 1, needed_fee passes the check at send.rs:211 for the
// underestimated size, but the real vsize yields an effective rate < 1 sat/vb,
// below DEFAULT_MIN_RELAY_TX_FEE → non-standard, unrelayable transaction.
```