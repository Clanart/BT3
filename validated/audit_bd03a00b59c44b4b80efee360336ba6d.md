### Title
Post-fee branch output amount handled with inverted subtraction, silently dropping all child payments — (`processor/src/multisigs/scheduler/utxo.rs`)

### Summary
The referenced bug class is "gross amount stored/used where the net (post-fee) amount was required." The direct analog lives in `Scheduler::created_output`, which is informed of a branch output's expected (pre-fee) amount and its actual (post-fee) amount, then computes the fee to amortize as `actual - expected` — the subtraction is inverted. Since `actual` is always `<= expected` after fee amortization, this underflows, causing every payment scheduled under a fee-bearing branch output to be silently dropped (or the processor to panic in debug builds).

### Finding Description
When a Plan contains more payments than `MAX_OUTPUTS`, `Scheduler::execute` splits them: excess payments are queued in `queued_plans` keyed by their pre-fee sum, and a payment to the multisig's own branch address is inserted so a future output will fund them (`processor/src/multisigs/scheduler/utxo.rs:219-254`).

`Network::prepare_send` amortizes the transaction fee over all payments, including branch payments, and reports each branch as `PostFeeBranch { expected: initial_amount, actual: post-fee amount }` (`processor/src/networks/mod.rs:479-517`). `actual` is therefore strictly less than `expected` whenever a nonzero fee share lands on the branch payment.

The result is passed to `scheduler.created_output(txn, branch.expected, branch.actual)` (`processor/src/multisigs/mod.rs:762-775`), which computes:

```rust
let mut to_amortize = actual - expected;
```
`processor/src/multisigs/scheduler/utxo.rs:485`

The intended fee is `expected - actual`. With `actual < expected`, `actual - expected` underflows. In release builds the `u64` wraps to a huge value, so the subsequent check `payments_sum < to_amortize` is always true and the function returns early — dropping the entire queued set of payments without recording them anywhere (`utxo.rs:486-489`). In debug builds the underflow panics outright. The correct value would be `expected - actual`, mirroring the fix in the original report (use the post-fee/net amount).

### Impact Explanation
Every child payment underneath any branch output that absorbed a nonzero fee share is silently and permanently dropped. Those payments correspond to user burns awaiting payout; the branch UTXO still arrives on-chain, but `add_outputs` finds no matching entry in `self.plans` for it, so it degrades to a generic wallet UTXO while the associated burn obligations are never fulfilled — users who burned their coins receive nothing, a direct loss of owed funds. In debug builds the processor panics, halting signing operations (liveness failure).

### Likelihood Explanation
Triggering requires a scheduling round where payments exceed `N::MAX_OUTPUTS` (16 for Bitcoin) so a branch output is created, and the transaction fee amortized onto the branch payment is nonzero — the normal case, since `prepare_send` always subtracts a positive `tx_fee` distributed across payments (`networks/mod.rs:482-495`). Fee shares land on the branch payment whenever `tx_fee >= number of payments` or the branch payment receives the remainder, which is overwhelmingly common. Burns are permissionless public operations, so any busy period or deliberate batch of burns reaches this path without privileged access.

### Recommendation
Compute the amortizable fee as the expected minus the actual amount:

```rust
let mut to_amortize = expected - actual;
```

in `created_output` (`processor/src/multisigs/scheduler/utxo.rs:485`), so that payments queued under a branch have the branch-creation fee correctly amortized rather than triggering an underflow that discards them. Also add a regression test covering `created_output` with `actual < expected`.

### Proof of Concept
1. A Plan with `payments.len() > N::MAX_OUTPUTS` causes `execute` to queue `removed` payments under `queued_plans[amount]` and add a branch payment of `amount` (`utxo.rs:234-255`).
2. `prepare_send` computes `tx_fee > 0` and amortizes it across payments, producing `PostFeeBranch { expected: amount, actual: amount - fee_share }` (`networks/mod.rs:479-517`).
3. `scanner_event_to_multisig_event`/plan handling calls `created_output(txn, expected = amount, actual = amount - fee_share)` (`multisigs/mod.rs:775`).
4. Inside `created_output`, `to_amortize = actual - expected = (amount - fee_share) - amount` underflows to ~`u64::MAX - fee_share`.
5. `payments.iter().map(...).sum::<u64>() < to_amortize` is true for any real payment set, so the function returns at `utxo.rs:487-489`, permanently dropping every payment the branch output was meant to fund — while the branch output itself still arrives on-chain and is spendable only as unaccounted wallet balance.