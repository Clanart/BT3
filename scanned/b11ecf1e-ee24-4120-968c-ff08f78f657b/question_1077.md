# Q1077: MGPRelease.claim - the revoked flag is checked only at the top of claim

## Question
Consider rewards/MGPRelease.sol, where claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Assuming the contract balance is below the sum of unclaimed allocations, can an unprivileged attacker turn this into a divergence between `beneficiaries[account].claimed` and `getClaimable(account)` via `claim()`, breaking the invariant that the accessor and the settlement path must agree on whether an entitlement exists and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the revoked flag is checked only at the top of claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Precondition: the contract balance is below the sum of unclaimed allocations.
- Invariant to test: the accessor and the settlement path must agree on whether an entitlement exists; concretely, `beneficiaries[account].claimed` must stay reconciled with `getClaimable(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract balance is below the sum of unclaimed allocations, call `claim()`, and assert `beneficiaries[account].claimed` equals `getClaimable(account)` and that no account can withdraw more than it put in.
