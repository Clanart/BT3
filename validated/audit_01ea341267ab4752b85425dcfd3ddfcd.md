### Title
`SignableTransaction::new` omits the OP_RETURN data output from weight/fee calculation, producing transactions that do not achieve the specified fee rate and may exceed `MAX_STANDARD_TX_WEIGHT` - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the PoolTogether `maxDeposit` finding — where a function returned a value guaranteeing success while omitting a state component (the yield buffer) that made the subsequent operation fail — `SignableTransaction::new` computes transaction weight and `needed_fee` while omitting the OP_RETURN `data` output it has already committed to adding. The function contractually promises "the fee necessary for this transaction to achieve the fee rate specified at construction" (`needed_fee`, send.rs:129-135) and enforces `MAX_STANDARD_TX_WEIGHT`, but both are computed on a transaction that is smaller than the one actually constructed and signed.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` (send.rs:194-202), but weight estimation is performed via `Self::calculate_weight_vbytes(tx_ins.len(), payments, ...)` which builds its dummy transaction exclusively from `payments` — never `tx_outs` — and therefore never includes the `data` output (send.rs:204, send.rs:225-226). `calculate_weight_vbytes` itself only maps `payments` into outputs (send.rs:85-99). The consequences:

- `needed_fee = fee_per_vbyte * vbytes` (send.rs:206, 227) is computed on a transaction missing up to ~92 vbytes (8-byte amount + script for an 80-byte OP_RETURN push, the maximum permitted by the `TooMuchData` check at send.rs:171). The signed transaction's actual fee rate is `needed_fee / actual_vbytes < fee_per_vbyte`.
- The minimum-relay-fee guard at send.rs:211 uses the same understated `vbytes`, so a `fee_per_vbyte` marginally above the relay minimum yields a transaction whose true fee rate is below `DEFAULT_MIN_RELAY_TX_FEE` — it will be rejected by `sendrawtransaction`/mempool policy despite the constructor returning `Ok`.
- The `weight > MAX_STANDARD_TX_WEIGHT` check at send.rs:241 uses `weight` missing the data output, so a transaction over the standard weight limit can be constructed and signed, then rejected by the network.

This mirrors the report's shape exactly: a value returned/accepted under a stated guarantee (max deposit that won't revert; fee rate / weight bound that will be met) while an omitted component invalidates the guarantee at execution time.

### Impact Explanation
Any caller constructing a `SignableTransaction` with `data` set (the API explicitly supports up to 80 bytes) receives a transaction paying less than the requested `fee_per_vbyte`. With a change output, the paid fee is exactly the understated `fee_with_change` (send.rs:228-232); without change the leftover still funds the fee but the rate guarantee is still broken. At boundary fee rates the transaction fails relay (burning the plan/attempt, and for a threshold multisig stalling the signing round that produced it); at boundary sizes it is rejected as non-standard. The produced transaction is authoritatively committed via the deterministic `txid`/eventuality, so the failure is discovered only after signing.

### Likelihood Explanation
Requires `data` to be supplied to `SignableTransaction::new`. Note that `processor/src/networks/bitcoin.rs` currently passes `None` for `data` (processor/src/networks/bitcoin.rs:450), so the defective path is not exercised by the in-tree processor today; the bug exists in the in-scope `bitcoin-serai` wallet library and triggers deterministically whenever the documented `data` functionality is used with a change output or near-limit fee/size. Severity assessed as Medium: guaranteed violation of the function's fee-rate contract and potential broadcast failure, but no direct fund loss.

### Recommendation
Pass the final output set into the weight estimator — i.e., call `calculate_weight_vbytes` over `tx_outs` (payments + OP_RETURN + change) rather than `payments`, or add the data output into the dummy transaction. Concretely, build the dummy `tx.output` from the already-constructed `tx_outs` plus the optional change output so that `vbytes`, `needed_fee`, the minimum-fee check, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the transaction actually signed.

### Proof of Concept
```rust
// networks/bitcoin: construct a tx with max-size data and a tight fee rate
let data = vec![0u8; 80]; // passes the TooMuchData check
let tx = SignableTransaction::new(inputs, &payments, Some(change_addr), Some(data), fee_per_vbyte).unwrap();

// needed_fee was computed on a tx WITHOUT the OP_RETURN output (~84+ vbytes missing)
let actual_vbytes = tx.tx.vsize() as u64;
let actual_fee = tx.fee(); // == needed_fee when change exists
assert!(actual_fee < fee_per_vbyte * actual_vbytes); // achieved rate < specified rate
// With fee_per_vbyte at the relay floor, broadcast fails min relay fee policy.
// Similarly, weight exceeding MAX_STANDARD_TX_WEIGHT by the omitted output's weight
// passes the line-241 check yet is rejected by the network.
```