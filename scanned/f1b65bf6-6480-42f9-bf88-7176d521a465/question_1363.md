# Q1363: Airdrop.updateEndRemainingAllocation - the bonus denominator can be re-snapshotted at any time

## Question
Consider rewards/Airdrop.sol, where updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Assuming the attacker's allocation is small relative to the original totalRemainingAllocation, can an unprivileged attacker turn this into a divergence between `periodsEndTime[4]` and `block.timestamp` via `updateEndRemainingAllocation()`, breaking the invariant that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: the bonus denominator can be re-snapshotted at any time)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Precondition: the attacker's allocation is small relative to the original totalRemainingAllocation.
- Invariant to test: a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker's allocation is small relative to the original totalRemainingAllocation, call `updateEndRemainingAllocation()`, and assert `periodsEndTime[4]` equals `block.timestamp` and that no account can withdraw more than it put in.
