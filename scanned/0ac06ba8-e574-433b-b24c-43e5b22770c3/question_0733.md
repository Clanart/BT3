# Q0733: Airdrop.updateEndRemainingAllocation - front-running the first claim to fix the snapshot

## Question
In rewards/Airdrop.sol, because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Does `updateEndRemainingAllocation()` let an unprivileged caller exploit that under exactly one unclaimed allocation remains besides the attacker's, so that `sum of all allocations` diverges from `aidropToken.balanceOf(address(this))`, the invariant that the snapshot must not be influenceable by transaction ordering is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: exactly one unclaimed allocation remains besides the attacker's.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange exactly one unclaimed allocation remains besides the attacker's, call `updateEndRemainingAllocation()`, and assert `sum of all allocations` equals `aidropToken.balanceOf(address(this))` and that no account can withdraw more than it put in.
