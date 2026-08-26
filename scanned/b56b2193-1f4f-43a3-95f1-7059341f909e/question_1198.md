# Q1198: MGPRelease.claim - claimable is never bounded by the contract balance

## Question
In rewards/MGPRelease.sol, claim() transfers the computed figure with no comparison against the balance held, so once the sum of allocations exceeds the funded balance the later beneficiaries simply revert. Can an unprivileged attacker reach this through `claim()` while initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, and drive `sum of all totalAlloced` out of agreement with `IERC20(tokenToRelease).balanceOf(address(this))` - breaking the invariant that the sum of all claimable amounts must never exceed the tokens actually held - for Critical - Protocol insolvency?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claimable is never bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() transfers the computed figure with no comparison against the balance held, so once the sum of allocations exceeds the funded balance the later beneficiaries simply revert. Precondition: initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `sum of all totalAlloced` must stay reconciled with `IERC20(tokenToRelease).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, call `claim()`, and assert `sum of all totalAlloced` equals `IERC20(tokenToRelease).balanceOf(address(this))` and that no account can withdraw more than it put in.
