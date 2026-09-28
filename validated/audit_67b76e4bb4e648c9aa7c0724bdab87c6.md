### Title
Attacker-influenced payment amount causes `payment_sat + needed_fee` overflow, bypassing the `NotEnoughFunds` comparison guard or panicking the signer - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The vLLM bug class — a range-validation guard written as a bare comparison (`x < limit`) that silently evaluates favorably for an adversarial extreme value — maps onto `SignableTransaction::new`'s funds check. Instead of IEEE-754 `NaN`/`+Inf` evading `<`/`>`, here a `u64` value near `u64::MAX` causes `payment_sat + needed_fee` to wrap (release) or panic (debug), so `input_sat < (payment_sat + needed_fee)` either evaluates `false` when it must be `true`, or crashes the process. The same unchecked addition guards the change-output branch via `input_sat.checked_sub(payment_sat + fee_with_change)`, where the inner addition overflows before `checked_sub` ever runs.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`), the only rejection of under-funded transactions is:

```rust
// send.rs:215
if input_sat < (payment_sat + needed_fee) {
  Err(TransactionError::NotEnoughFunds { ... })?;
}
```

and the change decision is:

```rust
// send.rs:228
if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
  if value >= DUST { tx_outs.push(...); }
}
```

`payment_sat` is `payments.iter().map(|p| p.1).sum::<u64>()` (line 187), an unchecked `u64` sum over caller-provided amounts, and `payment_sat + needed_fee` / `payment_sat + fee_with_change` are plain `u64` additions with no `checked_add`. A payment amount near `u64::MAX` therefore:

- In debug / `overflow-checks = on` builds: panics inside `checked_sub`'s operand or the `<` guard — a crash reachable from transaction-construction inputs (analogous to the vLLM CUDA crash taking down the worker).
- In release builds: wraps to a small value, so `input_sat < wrapped` is `false` — the `NotEnoughFunds` guard is bypassed exactly as `NaN < 0` bypassed vLLM's `temperature < 0.0` guard. The wrapped change branch then yields `Some(huge value) >= DUST`, pushing a change `TxOut` worth far more than the inputs, producing a consensus-invalid transaction that `multisig()`/`TransactionMachine` will still drive the FROST threshold to sign. `fee()` (line 139) later computes `sum(inputs) - sum(outputs)` which underflows, again panicking.

Note the dust guard `*amount < DUST` (line 166) also uses a bare `<` — the exact same shape as vLLM's `temperature < _MAX_TEMP` — and upper-bound sanity on payment amounts is never enforced.

### Impact Explanation
An invalid payment amount propagated to `SignableTransaction::new` either panics the signer mid-construction (denial of service for all in-flight plans sharing the scheduler) or produces a wrapped fee/change computation and a transaction that is consensus-invalid yet still gets routed into the FROST signing machines. Either way the guard's intent — "reject under-funded / oversized spends before signature aggregation" — is silently defeated by an edge value, matching the reported class where `Inf`/`NaN` pass comparison gates and corrupt downstream execution.

### Likelihood Explanation
Payment amounts originate from user-initiated withdrawal/payment instructions processed by the scheduler into `Plan` payments, and `send.rs` performs no upper bound on `payment.1`. Whether upstream balance accounting caps individual payment amounts before this point is not visible in the in-scope code; if a malformed or oversized instruction (or a bug upstream) ever delivers a `u64`-extreme amount, the overflow is deterministic — no brute force required, unlike the negligible-probability edge cases dismissed elsewhere in this codebase (e.g., `hash_binding_factor` returning 0).

### Recommendation
Replace the bare comparisons/additions with checked arithmetic and explicit bounds, mirroring the advisory's `math.isfinite` fix:

```rust
let total_out = payment_sat
  .checked_add(needed_fee)
  .ok_or(TransactionError::NotEnoughFunds { inputs: input_sat, payments: payment_sat, fee: needed_fee })?;
if input_sat < total_out { Err(...)?; }

// change branch
if let Some(total) = payment_sat.checked_add(fee_with_change) {
  if let Some(value) = input_sat.checked_sub(total) { ... }
}
```

Also cap each payment at `Amount::MAX_MONEY`-equivalent (21M BTC) alongside the existing `DUST` floor, and use `checked_add`/`saturating` for the `sum::<u64>()` accumulations of inputs and payments.

### Proof of Concept
```rust
// Against SignableTransaction::new with a single input of 1_000_000 sats
let payments = vec![(dest_script, u64::MAX - 10)];
// line 187: payment_sat = u64::MAX - 10 (sum fits)
// line 215: payment_sat + needed_fee  ->  wraps to a small number (release)
//           or panics (debug/overflow-checks)
// release: input_sat (1_000_000) < wrapped(~needed_fee-11) is FALSE -> guard bypassed
// line 228: payment_sat + fee_with_change wraps again -> checked_sub succeeds with
//           value ~= 1_000_000 - small, or the inner '+' itself panics in debug
// result: panics the signer, or emits a TX and later underflows fee() at send.rs:139
SignableTransaction::new(vec![received_output], &payments, Some(change), None, 5);
```