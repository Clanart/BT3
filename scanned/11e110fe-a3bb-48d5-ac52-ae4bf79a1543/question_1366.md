# Q1366: MGPRelease.claim - claim proceeds when claimable is zero

## Question
rewards/MGPRelease.sol: claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Under the beneficiary was revoked after having already claimed part of the allocation, is there an unprivileged sequence of `claim()` that leaves `sum of all totalAlloced` unreconciled with `IERC20(tokenToRelease).balanceOf(address(this))`, violates the invariant that a claim that moves no value must revert rather than emit, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim proceeds when claimable is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Precondition: the beneficiary was revoked after having already claimed part of the allocation.
- Invariant to test: a claim that moves no value must revert rather than emit; concretely, `sum of all totalAlloced` must stay reconciled with `IERC20(tokenToRelease).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the beneficiary was revoked after having already claimed part of the allocation, call `claim()`, and assert `sum of all totalAlloced` equals `IERC20(tokenToRelease).balanceOf(address(this))` and that no account can withdraw more than it put in.
