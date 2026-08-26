# Q2419: Airdrop.updateEndRemainingAllocation - the bonus denominator can be re-snapshotted at any time

## Question
Consider rewards/Airdrop.sol, where updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Assuming the attacker calls updateEndRemainingAllocation and claim in the same transaction, can an unprivileged attacker turn this into a divergence between `getBonusAmount(user)` and `allocations[user]` via `updateEndRemainingAllocation()`, breaking the invariant that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: the bonus denominator can be re-snapshotted at any time)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Precondition: the attacker calls updateEndRemainingAllocation and claim in the same transaction.
- Invariant to test: a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `updateEndRemainingAllocation()` sequence atomically under the attacker calls updateEndRemainingAllocation and claim in the same transaction, asserting at the end that `getBonusAmount(user)` still equals `allocations[user]` and the PoC's balance delta is non-positive.
