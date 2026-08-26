# Q2189: Airdrop.updateEndRemainingAllocation - front-running the first claim to fix the snapshot

## Question
In rewards/Airdrop.sol, because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Does `updateEndRemainingAllocation()` let an unprivileged caller exploit that under the token balance held by the contract is below the sum of remaining claimable amounts, so that `periodsEndTime[4]` diverges from `block.timestamp`, the invariant that the snapshot must not be influenceable by transaction ordering is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: the token balance held by the contract is below the sum of remaining claimable amounts.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times) under the token balance held by the contract is below the sum of remaining claimable amounts, asserting on every row that the snapshot must not be influenceable by transaction ordering.
