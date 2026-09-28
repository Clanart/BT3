### Title
OP_RETURN `data` output excluded from weight/fee and standardness checks, producing under-priced or non-relayable transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The reported bug class is *resource accounting that ignores attacker-controlled input size*: GMX's withdrawal gas estimate ignored the user-supplied `longTokenSwapPath`/`shortTokenSwapPath` lengths, so the keeper paid unpriced execution cost. The Serai analog lives in `SignableTransaction::new`: the fee and standardness calculations are performed by `calculate_weight_vbytes`, which models the transaction using only `inputs`, `payments`, and an optional `change` output — it never accounts for the `OP_RETURN` output carrying the caller-supplied `data` (up to 80 bytes) that is actually pushed into `tx_outs`.

### Finding Description
In `SignableTransaction::new`, the `data` argument (up to 80 caller-controlled bytes) is appended to `tx_outs` as a zero-value `OP_RETURN` output at send.rs:194-202. However:

- The initial fee estimate calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at send.rs:204 — `payments` does not include the `OP_RETURN` output, and there is no `data` parameter at all in `calculate_weight_vbytes` (send.rs:62-99).
- The change-aware recalculation at send.rs:225-226 likewise omits the `OP_RETURN` output, so `fee_with_change` is under-priced too.
- The `DEFAULT_MIN_RELAY_TX_FEE` check at send.rs:211 uses the under-counted `vbytes`, so it can pass while the real transaction is below the relay minimum.
- The `MAX_STANDARD_TX_WEIGHT` check at send.rs:241 uses `weight` computed without the `OP_RETURN` output, so a transaction that is actually non-standard can be accepted and signed.

The omitted output costs roughly `(8 value bytes + ~1–2 script-len bytes + 1 opcode + 1–2 push bytes + 80 data bytes) * 4 WU` ≈ ~92 vbytes — entirely unpriced and unbounded by the weight check, scaled directly by attacker-controlled `data.len()`.

### Impact Explanation
The actual fee paid is `sum(inputs) - sum(outputs)` (send.rs:138-141), which equals `needed_fee`, while the real transaction is ~92 vbytes larger than what `needed_fee` was priced for. Consequences:

- The produced transaction's effective fee rate is strictly below `fee_per_vbyte`, and can fall below `DEFAULT_MIN_RELAY_TX_FEE` — nodes will refuse to relay it, so the threshold-signed transaction cannot reach miners. The inputs are locked behind a signed-but-unbroadcastable TX (funds not spendable via this output until a new signing round is run at corrected parameters).
- A transaction passing the `TooLargeTransaction` check while actually exceeding `MAX_STANDARD_TX_WEIGHT` is non-standard and unreleayable for the same reason.
- If no change address is given, the discrepancy is silently absorbed as a lower real fee rate with no error raised.

### Likelihood Explanation
Any party able to cause `SignableTransaction::new` to be invoked with a non-empty `data` payload triggers this deterministically — the miscalculation is unconditional once `data.is_some()`. Whether the TX actually fails relay depends on how close `fee_per_vbyte` is to the relay minimum; near-boundary fee rates (common for cost-sensitive batch/bridge transactions) make the relay failure realistic rather than theoretical. The weight-check bypass is purely a function of input/output sizes near the standardness boundary.

### Recommendation
Pass the serialized `OP_RETURN` output (or `data` length) into `calculate_weight_vbytes` and include it in the modeled `tx.output` list for both the no-change and with-change computations, so `vbytes`, `needed_fee`, the min-relay check, the change-amount derivation, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the true transaction. Alternatively, push the `OP_RETURN` TxOut into a combined outputs slice before computing weight.

### Proof of Concept
```rust
// Conceptual: construct a SignableTransaction with a maximal data payload.
let data = Some(vec![0xaa; 80]);
let stx = SignableTransaction::new(
    inputs,                 // e.g., a single ReceivedOutput
    &[(payment_script, 600)],
    Some(change_script),
    data.clone(),
    1,                      // fee_per_vbyte at/below relay minimum boundary
).unwrap();

let real_vbytes = stx.transaction().vsize() as u64;
// stx.needed_fee() == fee_per_vbyte * vbytes_with_NO_op_return
assert!(stx.needed_fee() < 1 * real_vbytes);
// Effective fee rate of the signed TX is below the requested rate, and
// below DEFAULT_MIN_RELAY_TX_FEE-scaled real vsize -> not relayable.
```
The assertion holds because `calculate_weight_vbytes` at send.rs:62-127 builds a `Transaction` containing only `payments` (+optional change), while the signed `self.tx` at send.rs:245-251 additionally carries the `OP_RETURN` output appended at send.rs:194-202.