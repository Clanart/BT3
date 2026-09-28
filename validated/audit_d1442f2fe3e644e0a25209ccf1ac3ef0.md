### Title
OP_RETURN data output excluded from fee/weight calculation lets attacker-supplied data produce under-fee or nonstandard transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes the transaction weight/vbytes — which drive both the fee (`needed_fee = fee_per_vbyte * vbytes`) and the `MAX_STANDARD_TX_WEIGHT` policy check — using only `payments`, before and without accounting for the attacker-influenced `OP_RETURN` output that is appended to `tx_outs` earlier. The resulting transaction is larger than what was priced, so it pays a lower effective fee rate than requested and can exceed the standardness weight limit while still passing the check.

### Finding Description
In `crypto`-adjacent wallet code, `SignableTransaction::new` builds `tx_outs` and appends an `OP_RETURN` output carrying up to 80 bytes of caller/attacker-supplied `data` at `networks/bitcoin/src/wallet/send.rs:194-202`. Only afterward does it compute `(weight, vbytes)` via `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` at `send.rs:204`, which builds a dummy transaction containing *only* the payment outputs (and optionally change at `send.rs:226`) — the `OP_RETURN` output is never part of that dummy transaction. Consequently:

- `needed_fee` at `send.rs:206` and the change path's `fee_with_change` at `send.rs:227` are computed on a smaller vsize than the final transaction's.
- The minimum-relay-fee check at `send.rs:211` and the `MAX_STANDARD_TX_WEIGHT` check at `send.rs:241` are enforced against the underestimated weight, not the real one.

An OP_RETURN output adds roughly `8 (value) + 1 (script len) + 82 (OP_RETURN + push of 80 bytes)` ≈ 91 bytes (~364 wu, ~91 vbytes) that is never priced or weighed.

### Impact Explanation
An unprivileged depositor can influence `data` (the InInstruction payload embedded in deposits is propagated into the OP_RETURN of Serai-constructed Bitcoin transactions). Because the fee policy is enforced on an underestimated size, the signed transaction:

- Pays a fee rate strictly below the requested `fee_per_vbyte`, and potentially below `DEFAULT_MIN_RELAY_TX_FEE` on the real vsize — the check at `send.rs:211` passes on the smaller size while the real transaction is rejected by relay policy.
- Can exceed `MAX_STANDARD_TX_WEIGHT` while passing the check at `send.rs:241`, producing a nonstandard transaction no node will relay.

Either way, the wallet emits a signed transaction committing `Prevouts::All` (binding real UTXOs) that cannot confirm, leaving scanned funds locked in a transaction that won't propagate — funds effectively unspendable until reconstructed, plus potential confusion when the signed `txid` is broadcast and rejected.

### Likelihood Explanation
Any deposit carrying a `data` payload triggers inclusion of the OP_RETURN output; every such transaction is mispriced by ~91 vbytes of weight. At low specified fee rates this deterministically pushes the effective rate below relay minimum; for large inputs/payment sets it can push weight past standardness. No special positioning beyond sending a data-bearing deposit is required.

### Recommendation
Include the OP_RETURN output in `calculate_weight_vbytes`. Pass the full output list (payments + OP_RETURN, and change when applicable) into the dummy transaction used for weight, or add the OP_RETURN output's serialized size to the computed weight before deriving `needed_fee` and performing the `TooLowFee`/`TooLargeTransaction` checks.

### Proof of Concept
Construct `SignableTransaction::new(inputs, payments, change, Some(vec![0u8; 80]), fee_per_vbyte)`. The OP_RETURN output is pushed to `tx_outs` at `send.rs:194-202`, yet `calculate_weight_vbytes` at `send.rs:62-99` builds its dummy `Transaction` only from `payments` and `change` — `data` is never an argument. Compare `tx.transaction().vsize()` (or `weight().to_wu()`) on the returned transaction against `needed_fee() / fee_per_vbyte`: the actual vsize exceeds the priced vsize by the OP_RETURN output's size, and `fee() / actual_vsize < fee_per_vbyte`. With `fee_per_vbyte` chosen so `needed_fee` just clears the `DEFAULT_MIN_RELAY_TX_FEE` bound, the real transaction falls below min relay fee and is rejected by the network.

Caveat: I verified this statically; I did not execute the transaction construction against a regtest node to confirm the rejection, and the exact byte delta depends on script serialization.