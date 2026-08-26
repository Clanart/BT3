# Q0860: MGPRelease.claim - the revoked flag is checked only at the top of claim

## Question
rewards/MGPRelease.sol: claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. With the exact block at which the linear release is evaluated, and how often it is repeated under attacker control and the beneficiary claims repeatedly inside one block, can an unprivileged caller sequence `claim()` so that `startTimestamp and endTimestamp` and `block.timestamp` no longer reconcile, violating the invariant that the accessor and the settlement path must agree on whether an entitlement exists and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the revoked flag is checked only at the top of claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() reverts on vesting.revoked but getClaimable ignores the flag entirely, so any integrator reading the accessor sees an entitlement the claim path will refuse. Precondition: the beneficiary claims repeatedly inside one block.
- Invariant to test: the accessor and the settlement path must agree on whether an entitlement exists; concretely, `startTimestamp and endTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the beneficiary claims repeatedly inside one block, snapshot `startTimestamp and endTimestamp` and `block.timestamp`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
