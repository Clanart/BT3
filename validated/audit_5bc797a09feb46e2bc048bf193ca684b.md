### Title
`SignableTransaction` fee/weight accounting desyncs from the actual transaction when an OP_RETURN `data` output is added, producing under-funded/oversized transactions that cannot confirm - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The Flatcoin bug class is asymmetric accounting: one side of a balance sheet is clamped while the other is not, so the tracked total desyncs from the actual balance and every subsequent operation reverts, permanently locking funds. In `SignableTransaction::new`, the weight/vbytes used for `needed_fee`, the `TooLowFee` minimum-relay check, and the `MAX_STANDARD_TX_WEIGHT` check are computed from `payments` only, while the transaction actually built and signed additionally contains the caller-supplied OP_RETURN `data` output (and, separately, the change output accounting is handled inconsistently). The tracked size therefore desyncs from the actual size, producing a signed transaction that pays less than the requested/relay fee rate or exceeds standardness limits — an on-chain artifact that nodes reject and that locks the consumed `ReceivedOutput` inputs into a plan that can never confirm.

### Finding Description
`SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150-256) builds `tx_outs` starting from `payments` and then, if `data` is supplied, pushes an OP_RETURN `TxOut` carrying up to 80 bytes (lines 194-202). However, both weight/vbytes computations ignore that output:

- Line 204: `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — computes `vbytes` from `payments`, not `tx_outs`, so the OP_RETURN output's ~13-91 bytes of weight are excluded.
- Line 206: `needed_fee = fee_per_vbyte * vbytes` — the fee charged (and enforced via `NotEnoughFunds` at line 215) is calibrated to the smaller transaction.
- Line 211: the `TooLowFee` minimum-relay check compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000`, again using the understated vbytes.
- Line 226: the change-aware `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` also omits the data output.
- Line 241: `weight > MAX_STANDARD_TX_WEIGHT` is checked against `weight`, which excludes the OP_RETURN output.

The struct is then returned with `tx.output = tx_outs` (which includes the OP_RETURN output) and the understated `needed_fee` (lines 245-254). The tracked accounting (`needed_fee`, `weight`, the relay/standardness checks) systematically diverges from the actual transaction: two quantities that must stay in sync do not, exactly as in Flatcoin where `stableCollateralTotal` is clamped to 0 while `marginDepositedTotal` absorbs the full profit.

An unprivileged caller reaches this purely through public inputs to `SignableTransaction::new`: `inputs` (any `ReceivedOutput`s it has been handed/derived), `payments`, `change`, `data`, and `fee_per_vbyte`. Choosing `data` of near-maximum length (80 bytes) and a leftover amount that exactly satisfies the understated minimum-fee/needed-fee checks yields a signed transaction whose true feerate falls below the node minimum relay fee, or whose true weight exceeds the computed one. Because `fee()` (lines 138-141) is defined as `sum(prevouts) - sum(outputs)`, the builder "succeeds" — the funds are fully committed — but the resulting transaction is rejected by the Bitcoin network, and the consumed inputs' value is stranded (analogous to the Flatcoin revert-brick: outputs/inputs that are booked but never usable).

### Impact Explanation
A transaction produced this way cannot be broadcast: either its effective feerate is below `DEFAULT_MIN_RELAY_TX_FEE` on the real (larger) vsize, or, in aggregate, the real weight exceeds what the code validated. The `ReceivedOutput` inputs committed to the plan are consumed into an unconfirmable transaction — funds reported as allocated/spent that are not actually spendable, mirroring the Flatcoin outcome of collateral tracked but unwithdrawable. Since the discrepancy scales with attacker-controlled `data` length (up to 80 bytes plus output overhead, i.e. up to ~100 vbytes of unpriced weight), the underpayment can be made arbitrarily close to — or below — relay acceptance on the real transaction.

### Likelihood Explanation
Requires only that a caller supply a `data` payload (a fully public, caller-controlled byte vector, unchecked beyond the 80-byte cap at line 171) together with a fee rate/leftover tuned so the understated checks pass while the real transaction fails them. No validator misbehavior, collusion, or leaked key is needed; the desync is deterministic in the size arithmetic. Severity is Medium: it requires the caller to actually embed data (the in-tree processor passes `None`, so the concrete trigger is a consumer of this public wallet API), and the failure mode is a stuck/unbroadcastable transaction rather than direct theft.

### Recommendation
Compute `vbytes`/`weight` from the actual `tx_outs` (i.e., include the OP_RETURN output) before deriving `needed_fee`, performing the `TooLowFee` check, and the `MAX_STANDARD_TX_WEIGHT` check — or add the data output's serialized size to the estimate. Align the two sides of the accounting so the fee and validity checks are evaluated against the transaction that is actually signed, the same fix pattern as Flatcoin's recommendation (cap/synchronize both sides rather than letting one diverge).

### Proof of Concept
```rust
// networks/bitcoin: public API path
let inputs: Vec<ReceivedOutput> = vec![scanned_output];          // e.g. 50_000 sats
let payments = [(p2tr_script_buf(key).unwrap(), 10_000u64)];
let data = vec![0u8; 80];                                        // max allowed

// vbytes is computed WITHOUT the OP_RETURN output (send.rs:204),
// so needed_fee and the TooLowFee check pass on a smaller tx.
let tx = SignableTransaction::new(inputs, &payments, None, Some(data), fee_per_vbyte)
    .unwrap();

// The real transaction contains the extra ~91-byte OP_RETURN output.
// Its true vsize > the vsize used to compute needed_fee and the
// DEFAULT_MIN_RELAY_TX_FEE check => the signed TX's actual feerate
// falls below relay minimum (or weight accounting is understated),
// so nodes reject it and the input value is stranded in an
// unconfirmable plan despite needed_fee()/TooLowFee having "passed".
assert!(tx.fee() < needed_fee_for_real_vsize); // tracked fee desynced from actual tx
```
Key lines: OP_RETURN appended to `tx_outs` but not to the measured `payments` (send.rs:194-204), fee derived from understated `vbytes` (send.rs:206), min-relay check on understated `vbytes` (send.rs:211), and final `weight` check excluding the data output (send.rs:241).