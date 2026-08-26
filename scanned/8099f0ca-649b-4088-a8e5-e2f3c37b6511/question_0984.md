# Q0984: MGPRelease.claim - claimable is never bounded by the contract balance

## Question
Consider rewards/MGPRelease.sol, where claim() transfers the computed figure with no comparison against the balance held, so once the sum of allocations exceeds the funded balance the later beneficiaries simply revert. Assuming the contract balance is below the sum of unclaimed allocations, can an unprivileged attacker turn this into a divergence between `vested` and `beneficiaries[account].totalAlloced - initialUnlockedAmount` via `claim()`, breaking the invariant that the sum of all claimable amounts must never exceed the tokens actually held and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claimable is never bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() transfers the computed figure with no comparison against the balance held, so once the sum of allocations exceeds the funded balance the later beneficiaries simply revert. Precondition: the contract balance is below the sum of unclaimed allocations.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `vested` must stay reconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract balance is below the sum of unclaimed allocations, then assert `vested` and `beneficiaries[account].totalAlloced - initialUnlockedAmount` end identical in both runs.
