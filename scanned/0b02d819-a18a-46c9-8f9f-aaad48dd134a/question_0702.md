# Q0702: Airdrop.updateEndRemainingAllocation - the bonus denominator can be re-snapshotted at any time

## Question
In rewards/Airdrop.sol, updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Does `updateEndRemainingAllocation()` let an unprivileged caller exploit that under exactly one unclaimed allocation remains besides the attacker's, so that `getBonusAmount(user)` diverges from `allocations[user]`, the invariant that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: the bonus denominator can be re-snapshotted at any time)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Precondition: exactly one unclaimed allocation remains besides the attacker's.
- Invariant to test: a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under exactly one unclaimed allocation remains besides the attacker's, then assert `getBonusAmount(user)` and `allocations[user]` end identical in both runs.
