# Q2442: Airdrop.updateEndRemainingAllocation - front-running the first claim to fix the snapshot

## Question
Consider rewards/Airdrop.sol, where because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Assuming the attacker calls updateEndRemainingAllocation and claim in the same transaction, can an unprivileged attacker turn this into a divergence between `sum of all allocations` and `aidropToken.balanceOf(address(this))` via `updateEndRemainingAllocation()`, breaking the invariant that the snapshot must not be influenceable by transaction ordering and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: the attacker calls updateEndRemainingAllocation and claim in the same transaction.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls updateEndRemainingAllocation and claim in the same transaction, call `updateEndRemainingAllocation()`, and assert `sum of all allocations` equals `aidropToken.balanceOf(address(this))` and that no account can withdraw more than it put in.
