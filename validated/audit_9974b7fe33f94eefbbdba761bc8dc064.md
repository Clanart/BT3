### Title
OP_RETURN data output excluded from transaction weight/fee accounting, allowing over-standard-weight transactions and underpaid fees - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to CVE-2024-57843 (a fixed header consuming part of a bounded buffer without being accounted in the size check), `SignableTransaction::new` computes the transaction's weight and virtual size on a template that omits the OP_RETURN `data` output, then uses that underestimated weight both to bound the fee and to enforce `MAX_STANDARD_TX_WEIGHT`. The extra output bytes overflow the accounted budget, producing a transaction larger and heavier than what was validated.

### Finding Description
`SignableTransaction::new` first pushes the user-supplied OP_RETURN output onto `tx_outs` (lines 193-202), but then calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at line 204 — passing `payments`, not `tx_outs`. The weight/vbytes template therefore contains only the payment outputs, never the data output:

- `needed_fee = fee_per_vbyte * vbytes` (line 206) is computed from a vbytes figure that excludes the data output's ~9+80 bytes (~89 bytes → ~89 vbytes, since OP_RETURN outputs have no witness discount).
- The same omission occurs in the change path: `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (lines 225-226) again excludes the data output.
- The `weight > MAX_STANDARD_TX_WEIGHT` check at line 241 validates the undercounted `weight`, not the real transaction weight.
- `input_sat < payment_sat + needed_fee` (line 215) validates funds against an underestimated fee.

The result is a signed transaction whose actual weight exceeds the value used for the standardness check, and whose realized feerate (`fee() / actual_vsize`) is lower than the `fee_per_vbyte` the caller requested — the same class as the Linux bug, where `virtnet_rq_dma`'s 16 bytes on top of the payload weren't accounted against the page bound.

### Impact Explanation
- An 80-byte data output adds ~356 weight units unaccounted. A plan near `MAX_STANDARD_TX_WEIGHT` passes the line 241 check yet produces a non-standard transaction that Bitcoin Core will reject from mempool relay (`tx-size` policy), stalling the withdrawal. On Serai's processor flow, `make_signable_transaction` panics on `TooLargeTransaction` "despite limiting inputs/outputs" — here the inverse occurs: a too-large transaction is silently produced and signed, then fails to broadcast, locking the plan's inputs behind an invalid completion path.
- The effective fee paid per vbyte is lower than requested; during fee-market spikes the transaction may be evicted or never confirm even though `needed_fee()` reported compliance with `DEFAULT_MIN_RELAY_TX_FEE`.
- Since `data`/`payments` originate from on-chain InInstructions/scheduled payments, an unprivileged party influencing plan contents (e.g., many maximal-size payments plus a data payload) can push the transaction over the standard weight bound.

### Likelihood Explanation
Exploitation requires the plan's payments to approach the standard weight limit while also carrying an OP_RETURN payload. The 80-byte cap limits the overrun to ~89 vbytes, so only transactions within ~356 weight units of the boundary are affected. The scheduler does bound input/output counts, but no code re-validates the real transaction's weight after construction, making the boundary case reachable with crafted payment script sizes (payment `ScriptBuf`s are arbitrary and directly inflate vbytes in `calculate_weight_vbytes`).

### Recommendation
Include the OP_RETURN output in the size template: build the weight-estimation transaction from `tx_outs` (payments + data output + change) rather than only `payments`, in both call sites of `calculate_weight_vbytes`. Equivalently, measure `tx.weight()` on the final assembled `Transaction` before constructing `SignableTransaction`, and enforce `MAX_STANDARD_TX_WEIGHT` and `needed_fee` on that value.

### Proof of Concept
```rust
// Conceptual, in networks/bitcoin crate context
// Craft payments whose script_pubkeys make the payment-only template's weight
// W just below MAX_STANDARD_TX_WEIGHT, e.g. W = MAX - 100.
let payments: Vec<(ScriptBuf, u64)> = vec![(large_script_buf, DUST); N];
let data = vec![0u8; 80]; // OP_RETURN payload adds ~356 WU not counted

let tx = SignableTransaction::new(inputs, &payments, None, Some(data), fee_rate).unwrap();
// Construction succeeds: the check at line 241 used weight(payments only) = W.
// tx.tx.weight() == W + ~356, which exceeds MAX_STANDARD_TX_WEIGHT.
// serialize(tx.tx).len() confirms the real transaction is non-standard,
// and tx.fee() / real_vbytes < fee_rate.
```

The root cause is visible directly: `calculate_weight_vbytes` is invoked with `payments` (lines 204, 226) while the `data` output is only ever appended to `tx_outs` (line 195), so every size-derived bound — fee, minimum-relay check, and weight limit — is evaluated against a transaction smaller than the one actually signed.