### Title
`SignableTransaction::new` omits the OP_RETURN data output from weight/vsize accounting, undercharging the fee and letting non-standard or non-relaying transactions be produced - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to CVE-2024-46800 (a child queue consumes/drops an item without updating the parent's accounting `q.qlen`, corrupting state), `SignableTransaction::new` appends the OP_RETURN `data` output to `tx_outs` but computes `weight`, `vbytes`, `needed_fee`, and the `MAX_STANDARD_TX_WEIGHT` check using `calculate_weight_vbytes`, which only models `inputs` + `payments` + optional `change`. The data output is material to the final transaction yet invisible to all accounting derived from it.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

- The data output is pushed to the real transaction's outputs at lines 193-202 before any weight estimation.
- `calculate_weight_vbytes` (lines 62-127) builds a scratch `Transaction` containing only `payments` and optionally `change`; it has no `data` parameter and never includes the OP_RETURN output.
- Both call sites pass only `tx_ins.len(), payments, change`: the initial estimate at line 204 and the `fee_with_change` estimate at lines 225-227.
- Consequently:
  - `needed_fee` (line 206) is `fee_per_vbyte` times an under-estimated vsize, so the actual fee rate (`fee()` at lines 138-141 = `sum(inputs) - sum(outputs)`, where the OP_RETURN contributes 0 value but real bytes) is strictly lower than the requested rate.
  - The `MAX_STANDARD_TX_WEIGHT` check at line 241 uses the under-estimated `weight`, so a transaction that is actually above the standardness limit passes validation.

An OP_RETURN output carrying up to 80 bytes of data adds roughly (8 value + ~1-3 script len + up to 82 script bytes) × 4 WU ≈ ~360 WU (~90 vbytes) of unaccounted weight.

### Impact Explanation
Two concrete failure modes, both reachable by any party whose `data` field is serialized into a transaction built via `SignableTransaction::new` (in Serai this is the `InInstruction` data path for Bitcoin payments):

1. **Transaction below relay policy.** `needed_fee` is checked against `DEFAULT_MIN_RELAY_TX_FEE` using the under-counted `vbytes` (line 211). For `fee_per_vbyte` near the minimum, the produced transaction's real fee rate falls under 1 sat/vB and is rejected by Bitcoin Core relay policy, so a payment Serai considers constructed and signed will not propagate.
2. **Non-standard transaction.** Near the weight boundary, the unaccounted ~360 WU can push the real weight above `MAX_STANDARD_TX_WEIGHT`, making the transaction unbroadcastable entirely. Funds committed as inputs are then locked in a transaction the network refuses to carry — the wallet's accounting says the outputs are spendable/sent when they are not.

### Likelihood Explanation
Reachability requires only a non-empty `data` argument (up to 80 bytes, fully attacker-influenced in the InInstruction flow) combined with either a near-minimum fee rate or a near-limit transaction weight. Both are plausible operating conditions rather than exotic edge cases. The bug is deterministic — it mis-accounts on every call with `data: Some(_)`.

### Recommendation
Pass the `data` output into `calculate_weight_vbytes` (e.g., add the OP_RETURN `TxOut` to the scratch transaction whenever `data` is `Some`) at both call sites, so `weight`, `vbytes`, `needed_fee`, `fee_with_change`, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the transaction actually produced.

### Proof of Concept
Conceptual, from `networks/bitcoin/src/wallet/send.rs`:

```rust
// 80-byte data is accepted (line 171 check passes)
let data = Some(vec![0u8; 80]);
let tx = SignableTransaction::new(inputs, &payments, None, data.clone(), 1 /* sat/vB */)?;
// needed_fee is computed for vbytes excluding the ~90-vbyte OP_RETURN output.
// tx.fee() / tx.transaction().vsize() < 1 sat/vB → rejected by DEFAULT_MIN_RELAY_TX_FEE.
// Equivalently, with inputs/payments sized near MAX_STANDARD_TX_WEIGHT,
// the real tx.weight() exceeds the limit while the check at line 241 passes.
```

Fix sketch: inside `new`, compute `weight`/`vbytes` from a scratch transaction that includes the OP_RETURN output `TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) }`, mirroring the output pushed at lines 194-202.