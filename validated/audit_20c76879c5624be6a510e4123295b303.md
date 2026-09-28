### Title
OP_RETURN data output excluded from fee/weight calculation causes underpaid fees and non-relayable transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Solidity's `.transfer` fails because a fixed stipend (2300 gas) is insufficient for recipients with real execution costs. The analog in `SignableTransaction::new` is a fixed fee computed from a transaction shape that omits the OP_RETURN data output: `calculate_weight_vbytes` is called with `payments` only, while the data output appended to `tx_outs` is never counted. The declared `fee_per_vbyte` is therefore silently underpaid, and the resulting transaction can fall below the network's minimum relay fee and never propagate.

### Finding Description
In `SignableTransaction::new`, an OP_RETURN output carrying caller-supplied `data` (up to 80 bytes) is pushed onto `tx_outs` at networks/bitcoin/src/wallet/send.rs:194-202. However, the weight/vbyte measurement at line 204 calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)`, which builds its template transaction from `payments` — the data output is excluded. `needed_fee = fee_per_vbyte * vbytes` (line 206) is thus computed against a smaller vbytes than the real transaction. The same omission occurs in the change-output recalculation at lines 225-227.

Additionally, the minimum-relay-fee guard at line 211 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the *same underestimated* `vbytes`, so both sides of the check are wrong in the same direction and the check passes even though the actual fee rate is below the relay minimum.

For a small transaction (e.g. 1 input, 1 payment ≈ ~150-200 vbytes), an 80-byte OP_RETURN adds ~89 vbytes (~30-60% larger). If the caller selects `fee_per_vbyte` at or near the minimum relay rate, the actual rate is roughly `min_rate * v_est / (v_est + 89)`, which is below `DEFAULT_MIN_RELAY_TX_FEE`, so standard nodes will reject the transaction from their mempool.

### Impact Explanation
`data` is an untrusted, caller-controlled input (a Serai `InInstruction` payload flows through this path for outputs carrying instructions). The produced `Transaction` is signed by the FROST threshold via `TransactionSignMachine::sign` and is consensus-valid, yet may be non-standard/non-relayable or confirm at a much lower effective fee rate than intended. Funds committed to the multisig's outputs remain locked in a transaction that the network will not propagate, requiring a fee-bump/replacement flow the code does not provide — analogous to `.transfer` permanently reverting for gas-hungry recipients: a hardcoded budget that does not cover the real cost.

### Likelihood Explanation
Any unprivileged party able to attach ~80 bytes of instruction data to a payment triggers maximal fee underestimation. Whether the transaction is actually dropped depends on the chosen `fee_per_vbyte` being near the relay minimum and the transaction being small; for larger transactions the effect is a proportionally smaller but still real underpayment. Medium likelihood, Medium impact.

### Recommendation
Include the OP_RETURN output in the weight/vbytes estimation — e.g. build the template `tx.output` from `tx_outs` (or pass `payments` plus the data output) in `calculate_weight_vbytes`, for both the initial estimate and the change-output recalculation. Alternatively, add the exact serialized size of the OP_RETURN output to `weight`/`vbytes` before computing `needed_fee`, and re-run the `TooLowFee` check against the corrected vbytes.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs semantics
let data = vec![0u8; 80]; // max allowed by the TooMuchData check
let tx = SignableTransaction::new(
    vec![one_input],
    &[(payment_script, 1000)],
    None,
    Some(data.clone()),
    1, // fee_per_vbyte == 1 sat/vb (>= min relay rate for estimated vbytes)
).unwrap();

// OP_RETURN output (~89 bytes) was added to tx_outs but never to the
// template tx in calculate_weight_vbytes (line 204 uses `payments`).
// Actual vbytes = estimated_vbytes + ~89; fee paid = 1 * estimated_vbytes.
// Effective rate ~= v_est/(v_est+89) sat/vb < 1 sat/vb relay minimum
// => bitcoin nodes reject the signed transaction from their mempool.
```
The discrepancy is directly visible: `tx.transaction().output` contains the OP_RETURN output while `needed_fee()` reflects only `inputs` and `payments`.