### Title
Unchecked u64 arithmetic on transaction amounts allows signing a transaction that burns inputs as an excessive miner fee / creates invalid transactions - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` performs plain `u64` summation and multiplication over attacker-influenceable values (`payments` amounts and `fee_per_vbyte`) without overflow checks. In release builds these wrap silently, bypassing the `NotEnoughFunds`/`TooLowFee` guards and producing a transaction whose true fee (`sum(inputs) − sum(outputs)`) is far larger than `needed_fee`, or whose outputs exceed the input total.

### Finding Description
Three arithmetic sites are unchecked:

- `input_sat = inputs.iter().map(|i| i.output.value.to_sat()).sum::<u64>()` (line 175)
- `payment_sat = payments.iter().map(|p| p.1).sum::<u64>()` (line 187)
- `needed_fee = fee_per_vbyte * vbytes` and `fee_with_change = fee_per_vbyte * vbytes_with_change` (lines 206, 227)
- `input_sat < (payment_sat + needed_fee)` (line 215)
- `fee()` subtracts summed outputs from summed inputs unchecked (lines 139–140)

The only overflow-safe check is `input_sat.checked_sub(payment_sat + fee_with_change)` in the change branch (line 228) — but `payment_sat + fee_with_change` itself is computed with wrapping addition, so `checked_sub` operates on an already-wrapped operand.

The analog to CVE-2024-51480: an integer overflow in argument-derived values produces a downstream result (here, the signed sighash/commitment) that diverges from the values the checks validated. The bytes being signed via `taproot_key_spend_signature_hash` (lines 373–390) commit to `tx.output` values built from the attacker-supplied amounts, while the sufficiency check ran on wrapped sums.

### Impact Explanation
An unprivileged party who can influence the `payments`/`fee_per_vbyte` inputs to a signing plan can cause the threshold group to sign a transaction that was never intended:

- If `fee_per_vbyte * vbytes` wraps to a value that still passes the `DEFAULT_MIN_RELAY_TX_FEE` comparison but is much smaller than the honest product, the `input_sat < payment_sat + needed_fee` check can pass while `input_sat − payment_sat` (the actual fee paid) is enormous — burning a large fraction of the multisig's Bitcoin to miners. This is direct, permanent fund loss via a signed transaction.
- If `payment_sat` wraps below `input_sat`, a transaction with outputs summing to more than the inputs is produced and signed (an invalid, unbroadcastable transaction), giving at minimum a signing-round DoS, and `fee()` will panic on the underflowing subtraction.

Note: the impact depends on the caller passing attacker-controlled amounts and/or fee rate into `SignableTransaction::new`; I was unable to trace the upstream call sites of `SignableTransaction::new` to confirm how much of `payments`/`fee_per_vbyte` an unprivileged depositor/withdrawer controls, which bounds the likelihood.

### Likelihood Explanation
Medium-Low. The arithmetic flaw is unconditional, but exploitation requires an unprivileged party to control `fee_per_vbyte` or multiple payment amounts in a plan the processor signs. If `fee_per_vbyte` is purely operator/integrator-set, the vector reduces to integrator-supplied misuse and would be out of scope; if it comes from an on-chain instruction or attacker-reachable estimator, the burn-fee path is directly exploitable.

### Recommendation
Use saturating/checked arithmetic throughout `SignableTransaction::new` and `fee()`:

- `input_sat`/`payment_sat`: `try_fold`/`checked_add`, and reject any payment `> Amount::MAX_MONEY` (21M BTC) so `Amount::from_sat` always receives a consensus-valid value.
- `needed_fee`/`fee_with_change`: `fee_per_vbyte.checked_mul(vbytes)` with an error on overflow.
- `payment_sat + needed_fee` and `payment_sat + fee_with_change`: `checked_add`, rejecting on overflow before comparing against `input_sat`.
- `fee()`: return an error/`Option` via `checked_sub` instead of panicking subtraction.

### Proof of Concept
```rust
// Pseudo-invocation against SignableTransaction::new with a 1-input plan.
// Assume 1 input worth 100_000 sats and attacker-controlled fee_per_vbyte.

// vbytes for a 1-in/2-out TX ~ 140; choose fee_per_vbyte so the product wraps:
// fee_per_vbyte = (u64::MAX / 140) + k such that
//   fee_per_vbyte * 140 wraps to a small value >= min relay fee,
//   e.g. wrapped needed_fee = 1_000 sats.

// Checks:
//   needed_fee (wrapped, e.g. 1_000) >= (DEFAULT_MIN_RELAY_TX_FEE * vbytes)/1000  -> passes
//   input_sat (100_000) < payment_sat (546) + needed_fee (1_000)  -> false, passes
//
// Result: tx_outs = [payment of 546 sats]; no change added because
//   input_sat.checked_sub(payment_sat + fee_with_change) either returns None
//   (wrapped sum > input_sat) or a dust-small value.
// fee() == 100_000 - 546 = 99_454 sats burned to miners,
// despite needed_fee() reporting only ~1_000 sats.
// The produced TransactionMachine signs this over
// Prevouts::All with TapSighashType::Default (lines 373-390).
```