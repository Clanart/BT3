### Title
`SignableTransaction` omits the OP_RETURN data output from fee/weight calculation, undercharging the configured fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes `needed_fee` and the size check using `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which builds a mock transaction containing only the payment outputs (and optionally change). The OP_RETURN output carrying up to 80 bytes of caller-supplied `data` is pushed into `tx_outs` at lines 194-202 but is never included in either `calculate_weight_vbytes` call (lines 204 and 225-226). The transaction that is actually signed and broadcast is therefore larger than the transaction the fee was priced on, so the effective fee rate is always strictly lower than `fee_per_vbyte` whenever `data` is set — the same "charged fee deviates from the configured rate" class as the reference finding.

### Finding Description
- Line 204: `let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);` — `payments` excludes `data`.
- Lines 225-226: when change is present, `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` again excludes the OP_RETURN output.
- Line 206: `needed_fee = fee_per_vbyte * vbytes` is derived from the underestimated `vbytes`.
- Line 241: the `MAX_STANDARD_TX_WEIGHT` check uses `weight`, also computed without the data output.
- The actual fee paid is `sum(prevouts) - sum(tx.output)` (lines 138-141), which includes the zero-value OP_RETURN output's real size on the wire, so `fee() / actual_vbytes < fee_per_vbyte`.

### Impact Explanation
Every transaction carrying `data` pays a fee rate below the configured `fee_per_vbyte`, with a shortfall of up to ~90 vbytes × `fee_per_vbyte` sats (80-byte payload plus output overhead). At the minimum relay rate, a transaction priced exactly at the floor can land below `DEFAULT_MIN_RELAY_TX_FEE` on the wire and fail relay/confirmation despite `TooLowFee` passing. `needed_fee()` also reports a value inconsistent with the fee the signed transaction actually requires, and the standardness weight check can pass for a tx whose real weight exceeds the limit once the data output is added. Funds (the change/leftover accounting) are computed off an incorrect verifier formula, matching the Medium-severity "incorrect fee" class.

### Likelihood Explanation
Any caller passing `Some(data)` to `SignableTransaction::new` (a public API of the in-scope `bitcoin-serai` wallet crate, exercised via `SendMachine`/signing flows) triggers the discrepancy deterministically; the magnitude grows with payload size and chosen fee rate. No attacker cooperation or privileged position is needed — the miscalculation is inherent to the code path.

### Recommendation
Include the OP_RETURN output when sizing the transaction: pass the fully constructed `tx_outs` (or the `data` length) into `calculate_weight_vbytes` for both the no-change and change variants so `vbytes`, `weight`, and `needed_fee` reflect the transaction actually signed. Recompute `needed_fee` from `vbytes_with_change` already handled; extend the same to data.

### Proof of Concept
In `networks/bitcoin/tests/wallet.rs` style:

```rust
let inputs = vec![output];                  // single ReceivedOutput
let payments = vec![(addr(), 1000)];
let data = Some(vec![0u8; 80]);             // max allowed OP_RETURN payload

let st = SignableTransaction::new(inputs, &payments, None, data.clone(), FEE).unwrap();
// st.tx.output contains the OP_RETURN output, but st.needed_fee was computed from
// calculate_weight_vbytes(..., payments, None) which lacks it.
// fee() / actual_weight.to_wu().div_ceil(4) < FEE — effective rate below configured rate.
```

The signed transaction is ~90 vbytes larger than the priced mock, so `needed_fee` undercharges and the on-chain fee rate is strictly below `fee_per_vbyte`.