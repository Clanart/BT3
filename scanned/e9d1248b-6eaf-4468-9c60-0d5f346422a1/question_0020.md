# Q0020: Airdrop.updateEndRemainingAllocation - the bonus denominator can be re-snapshotted at any time

## Question
In rewards/Airdrop.sol, updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Starting from a state where block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, can an unprivileged EOA use `updateEndRemainingAllocation()` to leave `totalEndRemainingAllocation` inconsistent with `totalRemainingAllocation`, violating the invariant that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: the bonus denominator can be re-snapshotted at any time)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Precondition: block.timestamp has just passed periodsEndTime[4] and no one has claimed yet.
- Invariant to test: a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, have the attacker run `updateEndRemainingAllocation()`, then assert the victim's claimable value and the `totalEndRemainingAllocation` versus `totalRemainingAllocation` relation are unchanged by the attacker's transaction.
