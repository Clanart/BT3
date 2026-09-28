### Title
OP_RETURN data output omitted from fee/weight calculation, causing systematic underpayment of the intended fee rate - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` pushes the `OP_RETURN` output into `tx_outs`, but the weight/vbytes estimation in `calculate_weight_vbytes` is computed solely from `payments` (plus optional `change`). The data output's size is never accounted for, so `needed_fee = fee_per_vbyte * vbytes` underpays relative to the real transaction size, and the minimum-relay-fee check is evaluated against an underestimated vsize.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, the constructor performs:

1. `tx_outs` is built from `payments`, then the OP_RETURN output is appended (lines 188-202):
```rust
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}
```
2. But the weight is computed as:
```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```
and `calculate_weight_vbytes` only builds outputs from `payments` and `change` (lines 85-99) — the OP_RETURN output is never included.

Consequences:
- `needed_fee = fee_per_vbyte * vbytes` (line 206) is computed on a transaction that is smaller than the one actually signed. With the maximum 80 bytes of data, the underestimation is ~91 vbytes (8-byte value + compactsize + script). The effective fee rate is therefore strictly lower than `fee_per_vbyte`.
- The minimum relay fee check at lines 211-213 uses the same underestimated `vbytes`, so a transaction whose *actual* fee rate is below `DEFAULT_MIN_RELAY_TX_FEE` can pass validation, be signed by the whole threshold group, broadcast, and then be rejected by the mempool — or evicted/not confirmed — leaving the multisig's UTXOs locked in an unconfirmable transaction until a replacement is constructed.
- The `NotEnoughFunds` check (line 215) and the change computation (`input_sat - payment_sat - fee_with_change`, line 228) also use the understated fee. When change is added, `needed_fee` is likewise wrong (line 232: `needed_fee = fee_with_change`, computed without the data output), so `needed_fee()` and `fee()` report inconsistent values.

The analog to the referenced class (mishandled/concealed movement of customer funds via faulty accounting) is concrete here: any counterparty who can cause the wallet to construct a transaction carrying `data` (e.g. a burn/withdrawal flow attaching metadata) produces a consensus-valid but economically defective transaction — silently paying a different (lower) fee than authorized, or producing a transaction the network will not relay.

### Impact Explanation
- Signed transaction pays a lower effective fee rate than the `fee_per_vbyte` the signer agreed to, violating the fee contract the signature commits to.
- At low `fee_per_vbyte`, the real fee rate can fall below the mempool minimum, making the threshold-signed transaction unrelayable — stalling fund movement despite a completed FROST signing round.
- `needed_fee()` misreports the fee to callers, which can feed into downstream accounting of how much value the transaction moves.

### Likelihood Explanation
Triggered whenever `SignableTransaction::new` is called with `Some(data)` — a routine code path for OP_RETURN-carrying transactions. The discrepancy is proportional to data length and is guaranteed (not probabilistic); whether it crosses the relay-fee boundary depends on the configured fee rate.

### Recommendation
Include the OP_RETURN output in the weight estimation: either pass the already-constructed `tx_outs` (or an explicit `data` length parameter) into `calculate_weight_vbytes`, and recompute the fee over the true output set in both the change and no-change branches. Alternatively, build the candidate transaction first (including data output) and derive `weight()`/`get_virtual_tx_size` from it directly, so estimation can never diverge from the signed artifact.

### Proof of Concept
```rust
// Conceptual: construct two transactions differing only in `data`
let payments = vec![(dest_script, 10_000u64)];
let tx_no_data = SignableTransaction::new(inputs.clone(), &payments, None, None, 10).unwrap();
let tx_with_data =
  SignableTransaction::new(inputs.clone(), &payments, None, Some(vec![0xAA; 80]), 10).unwrap();

// The real vsize increased by ~91 bytes, but:
assert!(tx_with_data.needed_fee() - tx_no_data.needed_fee() < 91 * 10);
// tx_with_data's effective fee rate = fee() / actual_vbytes < 10 sat/vb,
// even though the caller requested 10 sat/vb and the signature commits to it.
```

The root cause is confined to `SignableTransaction::new` / `calculate_weight_vbytes` in `networks/bitcoin/src/wallet/send.rs:150-256`: the data output is added to `tx_outs` but never to the estimated transaction.