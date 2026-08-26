# Q1651: Airdrop.updateEndRemainingAllocation - the bonus denominator can be re-snapshotted at any time

## Question
Consider rewards/Airdrop.sol, where updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Assuming the attacker's allocation is the largest remaining one, can an unprivileged attacker turn this into a divergence between `sum of all allocations` and `aidropToken.balanceOf(address(this))` via `updateEndRemainingAllocation()`, breaking the invariant that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: the bonus denominator can be re-snapshotted at any time)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Precondition: the attacker's allocation is the largest remaining one.
- Invariant to test: a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker's allocation is the largest remaining one, then assert `sum of all allocations` and `aidropToken.balanceOf(address(this))` end identical in both runs.
