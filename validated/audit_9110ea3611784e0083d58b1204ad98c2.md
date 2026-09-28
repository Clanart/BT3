### Title
Branch-output shortfall underflows `to_amortize`, silently dropping an entire cohort of queued payments - (File: processor/src/multisigs/scheduler/utxo.rs)

### Summary

In the Rio report, a deficit accumulated during settlement was charged entirely to the first withdrawal cohort instead of being spread across users. The analog in Serai is `Scheduler::created_output` for UTXO networks: when a branch output is created on-chain with less than the expected amount (the normal case whenever a transaction fee was amortized out of the branch payment), the fee deficit accounting subtracts in the wrong direction, `actual - expected` instead of `expected - actual`, producing a `u64` underflow that causes the entire set of child payments under that branch to be silently dropped — a subset of users absorbing a total loss rather than the deficit being amortized.

### Finding Description

`Plan` construction in `prepare_send` (`processor/src/networks/mod.rs`) amortizes the transaction fee across all payments, including payments destined for the multisig's own branch address, and records `PostFeeBranch { expected: initial_amount, actual: reduced_amount }`. Consequently, a branch output is routinely created with `actual < expected` whenever the fee share is nonzero.

When that branch output later appears on-chain, `created_output` is invoked with `expected` equal to the sum of the queued child payments and `actual` equal to the amount the output was actually created with. The amortization then computes:

```rust
// processor/src/multisigs/scheduler/utxo.rs:485
let mut to_amortize = actual - expected;
```

With `actual < expected`, this is a `u64` underflow. In release builds it wraps to a value near `u64::MAX`, making the guard

```rust
// processor/src/multisigs/scheduler/utxo.rs:487
if payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>() < to_amortize {
  return;
}
```

always true, so the function returns and permanently drops the entire `payments` set popped from `queued_plans` (line 473) — even when the real shortfall is a single satoshi. In debug builds the subtraction panics outright, halting the scheduler. The correct amortization term is `expected - actual` (the fee that must be spread across the child payments), matching the per-payment division loop that follows and the sanity check `assert!(actual >= payments.sum)` at line 513.

Like the Rio bug, the economic effect is that a deficit is borne entirely — and here, catastrophically — by one cohort of users (all payments queued under that branch output), while every other payment cohort is unaffected, instead of the fee being spread per the amortization loop.

### Impact Explanation

All payments queued under a branch output created with even a marginally reduced value are silently discarded: the users' funds remain inside the multisig's UTXO set but the corresponding payouts are never scheduled or executed — funds effectively burned/locked for that cohort. A `debug_assertions` build additionally causes a scheduler panic (liveness failure) on the same path. No attacker needs key material or validator collusion; the trigger is the ordinary fee-amortization path whenever `actual < expected` reaches `created_output`.

### Likelihood Explanation

The trigger requires a branch output being created for less than the `expected` queued amount, which is the designed outcome of fee amortization in `prepare_send` (branch payments are reduced by their fee share and recorded via `PostFeeBranch`). Branches are created whenever scheduled payments exceed `N::MAX_OUTPUTS`, so this path is exercised under payment load. The main caveat is that I could not trace every caller of `created_output` to confirm whether `actual` may ever be normalized to `expected` before this call; if some upstream path always passes `actual >= expected`, the underflow never fires and the remaining amortization logic is correct (the `expected - actual` reading). The strong textual signal — the log message "output expected to have {} had {:?} after fees" — indicates `actual < expected` is an anticipated input, which makes the reversed subtraction a live bug.

### Recommendation

Compute the shortfall in the correct direction:

```rust
let mut to_amortize = expected - actual; // or use checked arithmetic and handle actual > expected
```

Additionally, use `checked_sub`/saturating arithmetic so that an unexpected `actual < expected` cannot wrap, and consider re-queueing or explicitly accounting for dropped payments rather than silently returning. The `expected == payments.sum` assertion at line 474 should also use `>=` semantics consistent with dust-dropped payments if that state is reachable.

### Proof of Concept

Conceptual trace (unit-testable against `Scheduler<N>` for a UTXO network):

1. Queue more than `N::MAX_OUTPUTS` payments so `execute` creates a branch payment of amount `X = sum(child_payments)` and registers `queued_plans[X] = child_payments` (utxo.rs:219-255).
2. `prepare_send` amortizes a nonzero fee, so the on-chain branch output is created with `actual = X - fee_share` (networks/mod.rs:479-517).
3. `created_output(txn, X, Some(X - fee_share))` is invoked:
   - `to_amortize = (X - fee_share) - X` underflows to ~`u64::MAX`.
   - `sum(child_payments) = X < u64::MAX` → early `return`.
   - All `child_payments` are dropped; the branch output's funds are never disbursed.

With `fee_share = 1` satoshi, an entire branch worth of user payments is forfeited — the deficit is paid 100% by that cohort, exactly the unfair-single-cohort-payment class of M-10, here compounded by a sign error into total loss rather than proportional amortization.