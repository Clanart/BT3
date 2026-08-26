# Q0302: MGPRelease.claim - claim proceeds when claimable is zero

## Question
rewards/MGPRelease.sol: claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Under block.timestamp is exactly startTimestamp, is there an unprivileged sequence of `claim()` that leaves `sum of all totalAlloced` unreconciled with `IERC20(tokenToRelease).balanceOf(address(this))`, violates the invariant that a claim that moves no value must revert rather than emit, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim proceeds when claimable is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Precondition: block.timestamp is exactly startTimestamp.
- Invariant to test: a claim that moves no value must revert rather than emit; concretely, `sum of all totalAlloced` must stay reconciled with `IERC20(tokenToRelease).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under block.timestamp is exactly startTimestamp, then assert `sum of all totalAlloced` and `IERC20(tokenToRelease).balanceOf(address(this))` end identical in both runs.
