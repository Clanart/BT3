### Title
OP_RETURN outputs are omitted from fee and weight accounting, enabling underpriced or oversized transactions - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` appends an attacker- or caller-controlled OP_RETURN output to `tx_outs`, but calculates `vbytes`, `needed_fee`, and the maximum-standard-weight check from `payments` only, excluding the serialized data output. This is directly analogous to inconsistent amount/accounting state: the transaction actually signed and broadcast is larger than the transaction used to calculate its fee and standardness. A transaction that should fail the minimum-fee check or exceed `MAX_STANDARD_TX_WEIGHT` can therefore be accepted for threshold signing.

### Finding Description
When `data` is supplied, `SignableTransaction::new` creates an additional zero-value `TxOut` containing an OP_RETURN script and appends it to `tx_outs` at `networks/bitcoin/src/wallet/send.rs:193-202`. The initial fee is then calculated by calling `calculate_weight_vbytes(tx_ins.len(), payments, None)`, whose transaction contains only the payment outputs and omits the OP_RETURN output already queued in `tx_outs` at `networks/bitcoin/src/wallet/send.rs:204`. The resulting `vbytes` is used to derive `needed_fee` and perform the minimum-relay-fee check at `networks/bitcoin/src/wallet/send.rs:206-212`. The change-path calculation repeats the same omission by passing only `payments` and `change` at `networks/bitcoin/src/wallet/send.rs:223-232`. Finally, the incomplete `weight` is compared against `MAX_STANDARD_TX_WEIGHT`, while the transaction actually stored and signed contains `tx_outs`, including the omitted OP_RETURN output, at `networks/bitcoin/src/wallet/send.rs:241-251`.

### Impact Explanation
An unprivileged input supplying transaction data can cause the threshold wallet to sign a transaction whose actual serialized weight and vsize are greater than the values used to approve it. With a change output, the actual fee remains `input - payment - change = needed_fee`, so adding an up-to-80-byte OP_RETURN output reduces the effective feerate below the requested `fee_per_vbyte` and potentially below Bitcoin's minimum relay fee. The same omission can allow a transaction near the standard-weight boundary to pass the local `MAX_STANDARD_TX_WEIGHT` check even though the serialized transaction exceeds it. The result is concrete signing of an unintended transaction state: signers authorize a transaction represented as satisfying the requested fee and size constraints when it does not.

### Likelihood Explanation
The vulnerable path is reachable through the public `SignableTransaction::new` API by supplying nonempty `data`. No compromised validator, malicious peer behavior, invalid curve point, or leaked secret is required. The defect is deterministic because the OP_RETURN output is always present in `tx.output` but never included in the object passed to `calculate_weight_vbytes`.

### Recommendation
Include the complete output set, including the OP_RETURN output, when calculating both fee and weight. Conceptually, `calculate_weight_vbytes` should accept the final `tx_outs` or take `data: Option<&Vec<u8>>` and reproduce the exact output structure that will be signed. The same corrected calculation must be used for the no-change fee, the with-change fee, and the `MAX_STANDARD_TX_WEIGHT` check. Tests should assert that `SignableTransaction::needed_fee() / actual_tx_vsize` meets the requested fee rate and that the actual transaction weight, rather than the payment-only estimate, is checked.

### Proof of Concept
The following test demonstrates the accounting discrepancy:

```rust
let input = send_and_get_output(&rpc, &scanner, key).await;
let addr = p2tr_script_buf(key).unwrap();

// Input and payment chosen so the transaction has economically meaningful change.
let payments = [(addr.clone(), 10_000)];

// This adds a serialized output of roughly 90 vbytes.
let data = vec![0; 80];

let tx = SignableTransaction::new(
  vec![input],
  &payments,
  Some(addr),
  Some(data),
  1, // sat/vbyte
).unwrap();

let actual_tx = tx.transaction();
let actual_vbytes = u64::try_from(actual_tx.vsize()).unwrap();

// The stored transaction includes the OP_RETURN output.
assert!(actual_tx.output.iter().any(|output| output.script_pubkey.is_op_return()));

// The charged fee does not account for that output's vsize. Consequently,
// the actual feerate is below the requested 1 sat/vbyte and can fall below
// the minimum relay rate checked earlier in the function.
assert!(tx.needed_fee() < actual_vbytes);
```

At `networks/bitcoin/src/wallet/send.rs:193-202`, `tx_outs` contains the OP_RETURN output before `vbytes` is calculated at `networks/bitcoin/src/wallet/send.rs:204`. Because the calculation receives `payments` rather than `tx_outs`, `actual_tx.vsize()` is necessarily larger than the `vbytes` used to produce `needed_fee`.