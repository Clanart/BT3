# Q1393: MGPRelease.claim - claimable is never bounded by the contract balance

## Question
In rewards/MGPRelease.sol, claim() transfers the computed figure with no comparison against the balance held, so once the sum of allocations exceeds the funded balance the later beneficiaries simply revert. Starting from a state where the beneficiary was revoked after having already claimed part of the allocation, can an unprivileged EOA use `claim()` to leave `startTimestamp and endTimestamp` inconsistent with `block.timestamp`, violating the invariant that the sum of all claimable amounts must never exceed the tokens actually held and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claimable is never bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() transfers the computed figure with no comparison against the balance held, so once the sum of allocations exceeds the funded balance the later beneficiaries simply revert. Precondition: the beneficiary was revoked after having already claimed part of the allocation.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `startTimestamp and endTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the beneficiary was revoked after having already claimed part of the allocation, call `claim()`, and assert `startTimestamp and endTimestamp` equals `block.timestamp` and that no account can withdraw more than it put in.
