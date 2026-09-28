That file is out of scope (`processor/`, not `networks/bitcoin/src`). Let me check the in-scope wallet code for the same class.### Title
`SignableTransaction::new` omits the OP_RETURN output from the fee/weight calculation - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` builds `tx_outs` (payments + optional OP_RETURN data output), but computes `weight`/`vbytes` — and therefore `needed_fee` — via `calculate_weight_vbytes(tx_ins.len(), payments, None)`, which only ever materializes the `payments` outputs plus optional `change`. The OP_RETURN output appended at lines 194-202 is never reflected in the weight, the vsize-derived fee, or the `MAX_STANDARD_TX_WEIGHT` check. This is the same bug class as the reported finding: a conditional branch mutates the final state (an output is added / debt changes) while a required accounting update (the fee/weight recalculation, analogously `updateXP`) is skipped.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

- The `data` output is pushed onto `tx_outs` before the weight is computed:
  `send.rs:194-202` — `tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })`.
- But `needed_fee` is derived from `payments` only:
  `send.rs:204` — `let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);`
  `send.rs:206` — `let mut needed_fee = fee_per_vbyte * vbytes;`
- `calculate_weight_vbytes` (`send.rs:62-127`) builds a scratch `Transaction` containing only the payment outputs (plus `change` when supplied); there is no parameter for the data output, so it can never be counted.
- The same omitted update propagates to the change path (`send.rs:225-226`) and to the weight bound check (`send.rs:241`), both of which also ignore the OP_RETURN bytes.

Concretely, for an 80-byte `data` payload, the OP_RETURN output adds roughly 90+ bytes (~360 weight units, ~90 vbytes) to the signed transaction that were never priced. `needed_fee` is understated by `~90 * fee_per_vbyte` sats, and `fee()` (`send.rs:138-141`) will report a higher realized fee than the caller intended — the "missing" fee comes out of the change amount or, when there is no change output, silently becomes extra fee paid to miners.

### Impact Explanation
Any caller passing non-`None` `data` gets a transaction whose declared `needed_fee`/`vsize` accounting excludes the data output's bytes. The transaction's actual fee rate ends up `~90 * fee_per_vbyte` sats higher than intended (charged against change or burned as excess fee), or — in the inverse direction — the minimum-relay-fee check at `send.rs:211` passes on an understated vsize, so a transaction relying on `data` can be constructed that pays less than the required relay fee for its true size and will be rejected by the network. Funds become stuck in an unbroadcastable/unconfirmed transaction until reconstructed (medium severity; requires only attacker/user-supplied `data` bytes, all public inputs).

### Likelihood Explanation
High: the trigger is a single supported code path — `SignableTransaction::new(inputs, payments, change, Some(data), fee_per_vbyte)` with `data.len() <= 80`. No privilege, collusion, or malformed crypto is needed; the miscount occurs on every call that includes data, deterministically.

### Recommendation
Include the serialized OP_RETURN output in the weight/vbytes computation — e.g., extend `calculate_weight_vbytes` to accept the full output list (or the data length) so that `send.rs:204` and `send.rs:225-226` price the transaction that is actually built, mirroring the report's fix of updating XP whenever debt changes: every branch that appends to `tx_outs` must update the fee/weight accounting.

### Proof of Concept
```rust
// Conceptual PoC against networks/bitcoin/src/wallet/send.rs
let data = vec![0u8; 80];
let tx_with_data = SignableTransaction::new(
    inputs.clone(), &payments, None, Some(data.clone()), FEE,
).unwrap();
let tx_no_data = SignableTransaction::new(
    inputs, &payments, None, None, FEE,
).unwrap();

// needed_fee is identical despite the data version being ~90 vbytes larger
assert_eq!(tx_with_data.needed_fee(), tx_no_data.needed_fee()); // BUG: should differ
// tx_with_data.transaction().vsize() - tx_no_data.transaction().vsize() ~= 91 vbytes
// => actual paid fee exceeds needed_fee by ~91 * FEE sats, or relay-fee check at
//    send.rs:211 was validated against a vsize ~91 bytes too small.
```
Root cause lines: `send.rs:194-202` (output appended), `send.rs:204-206` (fee computed from `payments` only), `send.rs:62-99` (scratch tx lacks the data output), `send.rs:241` (weight bound also understated).

Note on scope: the strongest structural match found — the `outputs` accumulation/`continue` bug in `processor/src/networks/bitcoin.rs:686-737` where `presumed_origin`/`data` from a later transaction are applied to outputs scanned from an earlier one — lies outside the permitted scope (`processor/` is not in `networks/bitcoin/src`), so the in-scope `send.rs` fee-omission analog is reported instead.