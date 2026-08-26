# Q1043: Airdrop.updateEndRemainingAllocation - the bonus denominator can be re-snapshotted at any time

## Question
Consider rewards/Airdrop.sol, where updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Assuming totalBonus has grown large from earlier forfeits, can an unprivileged attacker turn this into a divergence between `getClaimableAmount(user)` and `allocations[user]` via `updateEndRemainingAllocation()`, breaking the invariant that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: the bonus denominator can be re-snapshotted at any time)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `updateEndRemainingAllocation()`: constrain the setup so that totalBonus has grown large from earlier forfeits, fuzz the attacker inputs (the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times), and assert after every call that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular.
