### Title
`SignableTransaction::new` computes vbytes/weight without the OP_RETURN `data` output, underpaying the fee rate and skipping the data output in the standard-weight check - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the Taurus `_computeCR` bug (a formula that silently drops a scaling term — there, the collateral decimals; here, an entire output's contribution to transaction size), `SignableTransaction::new` pushes the OP_RETURN `data` output into `tx_outs` but then computes `vbytes`/`weight` via `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which rebuilds outputs only from `payments` and `change`. The data output is invisible to the fee, minimum-relay-fee, and max-weight checks.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

- Lines 194-202: if `data` is specified, a zero-value `TxOut` with `ScriptBuf::new_op_return(...)` is pushed to `tx_outs`.
- Line 204: `let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);` — the helper at lines 62-127 constructs its sizing transaction from `payments` (and optional `change`) only; the OP_RETURN output already pushed to `tx_outs` is never included.
- Line 206: `needed_fee = fee_per_vbyte * vbytes` uses the understated `vbytes`.
- Line 211: the `TooLowFee` check `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` uses the same understated `vbytes`.
- Lines 224-234: the change path calls `calculate_weight_vbytes(..., payments, Some(&change))`, again excluding the data output.
- Line 241: `weight > MAX_STANDARD_TX_WEIGHT` is checked against `weight` that excludes the data output.

`data` is caller-supplied untrusted bytes (up to 80 bytes, checked at line 171) — e.g., the OP_RETURN `Shorthand`/`RefundableInInstruction` payloads Serai emits. The produced transaction's actual serialized size is larger than what every size-dependent computation assumed, by the full weight of the OP_RETURN output (roughly 4 × (9 + scriptPubKey bytes) weight units, up to ~360 WU / ~90 vbytes for maximal data).

### Impact Explanation
Two concrete consequences:

1. **Underpaid fee rate**: the transaction pays `needed_fee = fee_per_vbyte * vbytes_underestimated`, but relays at `needed_fee / vsize_actual`, a strictly lower rate than requested. Worse, the `TooLowFee` guard (line 211) can pass because it divides by the underestimated `vbytes`, while the real transaction's absolute fee fails the 1000 sat/kvB minimum relay rule once the data output is counted. The result is a `SignableTransaction` that "succeeds" construction yet will be rejected from mempool relay — funds are not received where the protocol believes a valid spend exists.
2. **Standard weight bound bypassed**: `MAX_STANDARD_TX_WEIGHT` is enforced against `weight` missing the data output, so a borderline transaction can be constructed that exceeds the standard weight limit and will not relay.

Like the Taurus finding, the formula is correct in shape but omits a term belonging to a non-default configuration (non-18 decimals there; a present `data` output here), so everything downstream (fee sufficiency, relay validity, size standardness) is computed on a wrong quantity.

### Likelihood Explanation
Any caller that passes `Some(data)` triggers it — data of even 1 byte is unaccounted, and up to 80 bytes is permitted. The error is deterministic, not adversary-timing-dependent; severity is bounded (Medium) because the effect is a transaction that underpays its intended rate / may fail relay or standardness rather than theft, and the processor's own usage passes `data: None` in `make_signable_transaction`, while library consumers supplying `data` hit the miscalculation directly.

### Recommendation
Include the data output in size accounting. Either:

- Push the OP_RETURN `TxOut` into the `payments`-style slice used for sizing, or change `calculate_weight_vbytes` to take the final `tx_outs` (or an extra `Option<&ScriptBuf>` for the data output) so `weight`, `vbytes`, `needed_fee`, the `TooLowFee` comparison, and the `MAX_STANDARD_TX_WEIGHT` check all see the complete output set.

```diff
- let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
+ // build sizing outputs = payments + optional data output + optional change
+ let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), &sizing_outputs, None);
```

### Proof of Concept
Conceptual differential, mirroring the report's PoC style:

```rust
// networks/bitcoin/src/wallet/send.rs
let payments = vec![(p2tr_script_buf(key).unwrap(), 100_000)];
let data = vec![0xAA; 80]; // maximal OP_RETURN payload

// Inside SignableTransaction::new:
// tx_outs gets 2 outputs (payment + OP_RETURN), but:
let (_, vbytes) = SignableTransaction::calculate_weight_vbytes(1, &payments, None);
// ^ sized with ONLY the payment output — OP_RETURN absent

let needed_fee = fee_per_vbyte * vbytes; // underestimates required fee
// The real tx.vsize() = vbytes + ~90, so the effective rate is
// needed_fee / (vbytes + ~90) < fee_per_vbyte.
// With fee_per_vbyte chosen so needed_fee just clears
// DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000, the actual transaction
// falls below the 1000 sat/kvB minimum relay fee and is not relayed,
// yet SignableTransaction::new returns Ok(..).
```

Relevant code: `calculate_weight_vbytes` sizing only `payments`/`change` (send.rs:62-99), data output pushed before sizing (send.rs:194-202), fee from understated vbytes (send.rs:204-206), `TooLowFee` check (send.rs:211), change-path sizing with same omission (send.rs:225-227), and weight check on understated `weight` (send.rs:241).