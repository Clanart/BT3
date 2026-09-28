### Title
Attacker-controlled OP_RETURN `data` output is excluded from the transaction weight/fee calculation, producing underpriced or non-standard transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The xml2rfc report is a case of untrusted document content reaching a sink that dereferences an attacker-supplied resource the rest of the pipeline never accounted for. The analog in Serai is in `SignableTransaction::new`: caller-supplied `data` is appended as a real `TxOut` on the final transaction, yet the fee, weight, and standardness calculations are all computed over `payments`/`change` only. An unprivileged party who can cause a Serai output-embedding transaction to be built (the `data` field is up to 80 bytes of externally supplied payload) forces the multisig to sign a transaction whose actual size and effective fee rate differ from what was committed to.

### Finding Description
`SignableTransaction::new` builds `tx_outs` from `payments`, then pushes an extra zero-value OP_RETURN output carrying `data` (up to 80 bytes) at `networks/bitcoin/src/wallet/send.rs:194-202`. However, the virtual-size computation calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — passing `payments`, not the full `tx_outs` — so the OP_RETURN output's ~23 extra vbytes are never counted (`send.rs:204`). The same omission occurs in the change branch at `send.rs:225-234`, which computes `vbytes_with_change` and `fee_with_change` over `payments` + `change` only.

Concretely:
- `needed_fee = fee_per_vbyte * vbytes` is underestimated by `fee_per_vbyte * ~23` sat for a full-size data payload.
- The change amount `input_sat - payment_sat - fee_with_change` is correspondingly too large only relative to intent; the real defect is the effective fee rate of the signed transaction is `needed_fee / actual_vbytes < fee_per_vbyte`.
- The standardness guard `weight > MAX_STANDARD_TX_WEIGHT` (`send.rs:241`) uses the weight computed without the data output, so a transaction whose true weight exceeds `MAX_STANDARD_TX_WEIGHT` (400,000 WU) will pass the check, be signed by the FROST multisig, and then be rejected by every Bitcoin node as non-standard.

### Impact Explanation
Two concrete outcomes, both reachable purely through public input bytes (`data` plus payment count/size):

1. **Underpriced transaction.** When the requested `fee_per_vbyte` is at or near the relay minimum, the uncounted data output weight drops the effective fee rate below `DEFAULT_MIN_RELAY_TX_FEE`, so the signed transaction fails relay/mempool acceptance even though `TooLowFee` was checked against the underestimated `vbytes`. The multisig produces a signature for a transaction that cannot be broadcast, stalling the spend of the committed inputs.
2. **Non-standard overweight transaction.** With many large payments plus a data output, the true weight can exceed `MAX_STANDARD_TX_WEIGHT` while the check passes, yielding a permanently non-relayable signed transaction and locked inputs until a corrected transaction is manually constructed.

Both are integrity failures in the artifact the threshold signature commits to: the signed transaction does not satisfy the fee/size policy the code claims to enforce.

### Likelihood Explanation
The `data` field is populated from externally supplied instruction payloads (an unprivileged party submitting a transfer with an attached message of up to 80 bytes — the cap enforced at `send.rs:171-173`). No key share, collusion, or privileged access is needed; the attacker only controls bytes that legitimately flow into `SignableTransaction::new`. Exploitation requires no race or probabilistic condition — the miscalculation is deterministic. The impact is bounded (liveness/policy failure rather than theft), so this rates Medium.

### Recommendation
Compute weight and fee over the actual output set, e.g. pass the constructed `tx_outs` (including the OP_RETURN output and the change output) into `calculate_weight_vbytes`, or add the data output's serialized size to `vbytes`/`weight` before computing `needed_fee` and before the `MAX_STANDARD_TX_WEIGHT` check. Specifically, change `send.rs:204` and `send.rs:226` to include the `data` output in the `payments`-equivalent list, or refactor `calculate_weight_vbytes` to take `&tx_outs`.

### Proof of Concept
```rust
// networks/bitcoin context
// Given: inputs totaling `input_sat`, fee_per_vbyte chosen so that
// needed_fee passes the TooLowFee check at the underestimated vbytes.

let inputs = vec![received_output];            // any scanned ReceivedOutput
let payments: &[(ScriptBuf, u64)] = &[];       // no payments
let data = Some(vec![0u8; 80]);                // attacker-supplied max-size payload

let stx = SignableTransaction::new(inputs, payments, Some(change_addr), data, FEE).unwrap();
// stx.tx.output has 2 entries: OP_RETURN(data) and change, but
// stx.needed_fee was computed as if only `payments` + change existed.
// actual_vbytes = needed_fee_vbytes + ~23, so effective rate < FEE.
// With FEE near the relay minimum, the signed tx is rejected by the mempool
// despite passing the TooLowFee and weight checks.
```

The relevant code: `tx_outs.push(TxOut { ... new_op_return ... })` at `send.rs:194-202`, the fee/weight computed from `payments` at `send.rs:204-206` and `send.rs:225-234`, and the weight check at `send.rs:241`, all in `networks/bitcoin/src/wallet/send.rs`.