### Title
`SignableTransaction::new` computes transaction weight and `needed_fee` while excluding the `OP_RETURN` data output, producing a systematically underpriced, non-replaceable transaction whose inputs are locked — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The bug class in M-9 is a state snapshot taken before all value is accounted for: `poolAmount` is snapshotted into per-recipient payouts at distribute time, so funds added afterwards are never credited and become locked. The same shape exists in `SignableTransaction::new`: the transaction's weight/vsize — which determines the fee reserved — is computed from a template that omits the `OP_RETURN` output, so the fee committed to the signing participants is calculated against a smaller transaction than the one actually signed. Because every input uses `Sequence::MAX` (no opt-in RBF), the resulting underpriced transaction cannot be fee-bumped, locking the spent inputs until it confirms.

### Finding Description
In `SignableTransaction::new`, the `OP_RETURN` output is appended to `tx_outs` at `send.rs:194-202`, *before* the weight calculation. However, both weight calculations are invoked with `payments` — the payment slice only — not `tx_outs`:

- `let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);` (`send.rs:204`)
- `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` (`send.rs:226`)

Inside `calculate_weight_vbytes` (`send.rs:62-127`), the template `Transaction` is built solely from `payments` plus the optional `change` output. The data output (up to 80 bytes per the `TooMuchData` check at `send.rs:171`, i.e. ~10-90 serialized bytes ≈ up to ~23 vbytes) is never included. Consequently:

1. `vbytes`/`vbytes_with_change` underestimate the real transaction size.
2. `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`, `send.rs:227-232`) is lower than `fee_per_vbyte * actual_vbytes`.
3. `fee()` — `sum(prevouts) - sum(outputs)` (`send.rs:138-141`) — settles at exactly this underestimated value, since the data output carries zero value.
4. The minimum-relay-fee guard (`send.rs:211`) is checked against the underestimated `vbytes`, so a transaction that is actually below the relay minimum can be accepted and signed.

The signed transaction is then finalized with `sequence: Sequence::MAX` on every input (`send.rs:79` in the template and `send.rs:182` in the real `tx_ins`). `Sequence::MAX` does not signal BIP-125 replaceability, so the underpriced transaction cannot be replaced by the sender. The `Schnorr`/FROST signing path (`TransactionSignMachine::sign`, `send.rs:355-398`) commits all participants to `Prevouts::All` over these inputs for this exact txid, so the signed shares are useless for any corrected transaction.

### Impact Explanation
Any caller that supplies `data` receives a transaction paying a materially lower fee rate than the `fee_per_vbyte` it requested — off by `fee_per_vbyte * (data_serialized_size + ~11)` satoshis, up to ~`fee_per_vbyte * 25`. At congested fee rates the transaction can sit unconfirmed indefinitely; since inputs are `Sequence::MAX` (no opt-in RBF), the only recovery is a child-pays-for-parent spend of an output the sender controls (change), which isn't always present. The funds in all inputs are effectively locked for the duration — directly analogous to M-9's "funds added after the payout snapshot are locked." Medium severity: permanent loss isn't guaranteed, but funds are rendered unspendable for an unbounded period through normal use of a public API parameter.

### Likelihood Explanation
Deterministic whenever `data: Some(_)` is passed to `SignableTransaction::new` — no adversary or edge-case timing required. The magnitude scales with data length and the requested fee rate. Whether the transaction stalls depends on mempool conditions, but the fee is *always* wrong when data is present, and the `TooLowFee` check is always evaluated against too-small a size.

### Recommendation
In `SignableTransaction::new`, pass the fully-built `tx_outs` (including the `OP_RETURN` output) into `calculate_weight_vbytes`, or append the data output's serialized size to the computed weight. Concretely, change `calculate_weight_vbytes` to accept `tx_outs`-equivalent output list, and call it after the data output is pushed:

```rust
// after the OP_RETURN push at send.rs:194-202
let (mut weight, vbytes) =
  Self::calculate_weight_vbytes_with_outputs(tx_ins.len(), &tx_outs, None);
```

and similarly for the change variant at `send.rs:226`, ensuring the change output is measured alongside the data output. Alternatively, set `sequence` to an RBF-signaling value (`Sequence::ENABLE_RBF_NO_LOCKTIME`) so underpriced transactions remain replaceable.

### Proof of Concept
```rust
// Construct: one input of 10_000 sats, one payment of 5_000 sats,
// data = 80 bytes, fee_per_vbyte = 100, no change.
let tx = SignableTransaction::new(
    vec![input_10k],
    &[(payment_script, 5_000)],
    None,
    Some(vec![0u8; 80]),
    100,
).unwrap();

// The real transaction is ~91 bytes larger than the measured template:
let actual_vsize = tx.transaction().vsize() as u64;
let assumed_vsize = tx.needed_fee() / 100;           // vbytes used internally
assert!(actual_vsize > assumed_vsize);                // always holds when data.is_some()

// tx.fee() == tx.needed_fee() < 100 * actual_vsize → realized fee rate < requested.
// All inputs use Sequence::MAX → no BIP-125 → cannot be bumped if it stalls.
```

Note on uncertainty: I verified the omission directly at `send.rs:194-232` and the `Sequence::MAX` usage at `send.rs:79` and `send.rs:182`; I did not trace whether upstream callers in `processor/` ever pass `Some(data)` (the processor is outside the in-scope list), but `SignableTransaction::new` is a public API whose `data` parameter is caller-controlled, so the path is reachable within the in-scope crate.