# Q0953: MGPRelease.claim - claim proceeds when claimable is zero

## Question
Consider rewards/MGPRelease.sol, where claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Assuming the contract balance is below the sum of unclaimed allocations, can an unprivileged attacker turn this into a divergence between `initialUnlockedAmount` and `beneficiaries[account].claimed` via `claim()`, breaking the invariant that a claim that moves no value must revert rather than emit and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim proceeds when claimable is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Precondition: the contract balance is below the sum of unclaimed allocations.
- Invariant to test: a claim that moves no value must revert rather than emit; concretely, `initialUnlockedAmount` must stay reconciled with `beneficiaries[account].claimed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated) under the contract balance is below the sum of unclaimed allocations, asserting on every row that a claim that moves no value must revert rather than emit.
