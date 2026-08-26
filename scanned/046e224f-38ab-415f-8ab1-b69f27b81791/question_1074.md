# Q1074: Airdrop.updateEndRemainingAllocation - front-running the first claim to fix the snapshot

## Question
Consider rewards/Airdrop.sol, where because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Assuming totalBonus has grown large from earlier forfeits, can an unprivileged attacker turn this into a divergence between `totalEndRemainingAllocation` and `totalRemainingAllocation` via `updateEndRemainingAllocation()`, breaking the invariant that the snapshot must not be influenceable by transaction ordering and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up totalBonus has grown large from earlier forfeits, snapshot `totalEndRemainingAllocation` and `totalRemainingAllocation`, run the attacker's `updateEndRemainingAllocation()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
