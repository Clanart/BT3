# Q0361: Airdrop.updateEndRemainingAllocation - the bonus denominator can be re-snapshotted at any time

## Question
In rewards/Airdrop.sol, updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Can an unprivileged attacker reach this through `updateEndRemainingAllocation()` while most participants have already claimed so totalRemainingAllocation is small, and drive `totalBonus` out of agreement with `aidropToken.balanceOf(address(this))` - breaking the invariant that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: the bonus denominator can be re-snapshotted at any time)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Precondition: most participants have already claimed so totalRemainingAllocation is small.
- Invariant to test: a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `updateEndRemainingAllocation()` sequence atomically under most participants have already claimed so totalRemainingAllocation is small, asserting at the end that `totalBonus` still equals `aidropToken.balanceOf(address(this))` and the PoC's balance delta is non-positive.
