# Q2166: Airdrop.updateEndRemainingAllocation - the bonus denominator can be re-snapshotted at any time

## Question
In rewards/Airdrop.sol, updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Does `updateEndRemainingAllocation()` let an unprivileged caller exploit that under the token balance held by the contract is below the sum of remaining claimable amounts, so that `totalBonus` diverges from `aidropToken.balanceOf(address(this))`, the invariant that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: the bonus denominator can be re-snapshotted at any time)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Precondition: the token balance held by the contract is below the sum of remaining claimable amounts.
- Invariant to test: a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `updateEndRemainingAllocation()`: constrain the setup so that the token balance held by the contract is below the sum of remaining claimable amounts, fuzz the attacker inputs (the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times), and assert after every call that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular.
