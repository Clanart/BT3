# Q1913: Airdrop.updateEndRemainingAllocation - the bonus denominator can be re-snapshotted at any time

## Question
In rewards/Airdrop.sol, updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Can an unprivileged attacker reach this through `updateEndRemainingAllocation()` while the first honest claim transaction is pending in the mempool, and drive `totalEndRemainingAllocation` out of agreement with `totalRemainingAllocation` - breaking the invariant that a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: the bonus denominator can be re-snapshotted at any time)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: updateEndRemainingAllocation() is public, has no one-shot guard and no access control, and simply assigns totalEndRemainingAllocation = totalRemainingAllocation whenever block.timestamp has passed periodsEndTime[4], so it can be called again after other users have claimed. Precondition: the first honest claim transaction is pending in the mempool.
- Invariant to test: a denominator that fixes every participant's pro-rata share must be snapshotted exactly once and by no one in particular; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the first honest claim transaction is pending in the mempool, snapshot `totalEndRemainingAllocation` and `totalRemainingAllocation`, run the attacker's `updateEndRemainingAllocation()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
