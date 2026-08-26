# Q0550: MGPRelease.claim - claimable is never bounded by the contract balance

## Question
In rewards/MGPRelease.sol, claim() transfers the computed figure with no comparison against the balance held, so once the sum of allocations exceeds the funded balance the later beneficiaries simply revert. Can an unprivileged attacker reach this through `claim()` while block.timestamp is exactly endTimestamp, and drive `beneficiaries[account].claimed` out of agreement with `getClaimable(account)` - breaking the invariant that the sum of all claimable amounts must never exceed the tokens actually held - for Critical - Protocol insolvency?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claimable is never bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() transfers the computed figure with no comparison against the balance held, so once the sum of allocations exceeds the funded balance the later beneficiaries simply revert. Precondition: block.timestamp is exactly endTimestamp.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `beneficiaries[account].claimed` must stay reconciled with `getClaimable(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange block.timestamp is exactly endTimestamp, call `claim()`, and assert `beneficiaries[account].claimed` equals `getClaimable(account)` and that no account can withdraw more than it put in.
