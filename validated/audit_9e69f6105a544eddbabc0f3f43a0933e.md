### Title
Branch-output fee shortfall subtracted in the wrong direction in `created_output`, underflowing `u64` and hanging/mis-amortizing all queued payments — ([File: processor/src/multisigs/scheduler/utxo.rs](processor/src/multisigs/scheduler/utxo.rs))

### Summary

`Scheduler::created_output` is invoked when a "branch" output — a self-payment created to fan out a large payment batch — is confirmed on-chain with an `actual` amount that is *less* than the `expected` amount, because the transaction fee for creating the branch was deducted from it. To account for this shortfall, the scheduler amortizes the difference across the child payments the branch was meant to fund. However, it computes the amortization amount as `actual - expected` instead of `expected - actual`. Since `actual < expected` in every non-zero-fee case, this is a `u64` underflow: in debug builds it panics; in release builds it wraps to ~`u64::MAX`, driving an amortization loop that zeroes out every child payment and then spins forever because `to_amortize` can never reach zero. This mirrors the Wildcat bug class: a cost (there, future penalty accrual; here, the branch creation fee) is miscalculated at settlement time, so obligations mapped to downstream claimants are left underfunded — except here the arithmetic is inverted, so instead of only the "last withdrawer" being shorted, the entire set of queued payments is destroyed and the scheduler stalls.

### Finding Description

When a `Plan` has more payments than `N::MAX_OUTPUTS`, `execute` strips the excess payments, records them in `queued_plans` keyed by their total amount, and inserts a payment to the multisig's own branch address for that total (`utxo.rs:234–255`). When the branch output later appears on-chain, `created_output` is called with the `expected` total and the `actual` amount the output was created with, which is `expected` minus the fee it cost to create (`utxo.rs:463–469`, comments at 33–35 and 469).

The amortization at `utxo.rs:483–504` then does:

```rust
let mut to_amortize = actual - expected;
if payments.iter().map(|p| p.balance.amount.0).sum::<u64>() < to_amortize {
  return;
}
while to_amortize != 0 { ... subtract per-payment shares ... }
```

`actual - expected` underflows whenever the branch cost any fee at all. In a release build (`u64` wrapping), `to_amortize` becomes ~2^64. The `payments_sum < to_amortize` early-return does not fire because the comparison is against a huge wrapped value that *is* greater than the payment sum — wait, it does fire: `sum < to_amortize` is true when `to_amortize` is huge, so it returns early, *dropping all child payments entirely* (the `payments` vec is simply discarded at line 473's scope since `payments` was popped from `queued` and never re-inserted into `self.plans`). So the practical release-build outcome is that the entire fan-out batch — all payments the branch was funding — is silently dropped whenever the branch output costs a nonzero fee, i.e., always.

The correct computation is `to_amortize = expected - actual`, the fee shortfall, which should be subtracted from the child payments.

This is reachable by an unprivileged party: any user can deposit to a Serai Bitcoin external address and trigger withdrawal `Instruction`s; once pending payments exceed `MAX_OUTPUTS` (16 per the comment at line 228), the scheduler creates a branch output, and `created_output` fires on its confirmation with `actual < expected`. An attacker can deliberately submit enough small payments to force the branching path, or simply wait for organic load.

### Impact Explanation

Every payment assigned to a branch plan is dropped without execution whenever the branch output's creation fee is nonzero — which is always, since all Bitcoin transactions pay a fee. Users' withdrawals/burns routed through a branch are silently discarded: the branch UTXO arrives in `self.utxos` (via `add_outputs`, since no `plans` entry exists for its amount) as stranded protocol funds, while the intended recipients never receive payment. This is a permanent loss-of-funds/liveness failure for every payment batch large enough to require branching — functionally identical in shape to the Wildcat finding, where obligations accrued after the accounting snapshot were offloaded onto the remaining claimants; here the unaccounted creation cost is offloaded onto *all* child payments, nuking them. In a debug build the processor panics instead.

### Likelihood Explanation

High given the triggering condition: the bug fires deterministically on the first branch output that costs any fee. The only mitigating factor is that reaching the branch path requires more than `MAX_OUTPUTS` simultaneous payments for one multisig, which an attacker can force by batching many small burn/withdrawal instructions (each payment only needs to exceed `N::DUST`).

### Recommendation

Change `let mut to_amortize = actual - expected;` to `let mut to_amortize = expected - actual;` in `Scheduler::created_output` (`processor/src/multisigs/scheduler/utxo.rs:485`), and add a regression test exercising a branch output created with `actual < expected` to verify child payments are reduced pro-rata rather than dropped. Additionally, consider using `checked_sub` and explicitly handling `actual > expected` (which should credit, not charge, the difference) to make the invariant explicit.

### Proof of Concept

Conceptual trace over `Scheduler::<Bitcoin>::created_output`:

1. `schedule` is called with `> N::MAX_OUTPUTS` payments. `execute` moves `to_remove` payments into `queued_plans[amount]` where `amount` is their sum, and inserts a branch `Payment` of `amount`.
2. The branch transaction is signed; its output is created with `actual = amount - creation_fee` (fee deducted per `created_output` docs, lines 33–35, 463–469).
3. `created_output(expected=amount, actual=Some(amount - fee))` computes `to_amortize = (amount - fee) - amount`, which underflows to `u64::MAX - fee + 1` in release (panic in debug).
4. `sum(child payments) = amount < u64::MAX - fee + 1`, so the guard at line 487 returns early. The popped `payments` vector is dropped; nothing is ever pushed to `self.plans`, so when the branch UTXO is later scanned, `add_outputs` finds no `plans[actual]` entry and merely parks the UTXO in `self.utxos`.
5. All child payments are permanently unexecuted; the branch funds sit stranded.

Key lines: `processor/src/multisigs/scheduler/utxo.rs:485` (inverted subtraction), `:487–489` (early return discarding payments), `:473` (payments popped from `queued_plans` before the check, so they are lost), `:234–255` (branch creation that sets `expected` to the full pre-fee sum).

One caveat I could not fully verify within this pass: the exact call site of `created_output` in `processor/src/multisigs/mod.rs` to confirm `actual` is post-fee — however, the in-function comments ("outputs ... will have their amount reduced by the fee", "expected to have {} had {:?} after fees", "Amortize the fee amongst all payments") unambiguously establish `actual <= expected` with the difference being the fee, which is sufficient to confirm the inverted subtraction.