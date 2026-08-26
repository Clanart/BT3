# Q1626: Airdrop.claim - front-running the first claim to fix the snapshot

## Question
In rewards/Airdrop.sol, because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Starting from a state where the attacker's allocation is small relative to the original totalRemainingAllocation, can an unprivileged EOA use `claim()` to leave `getBonusAmount(user)` inconsistent with `allocations[user]`, violating the invariant that the snapshot must not be influenceable by transaction ordering and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: the attacker's allocation is small relative to the original totalRemainingAllocation.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker's allocation is small relative to the original totalRemainingAllocation, snapshot `getBonusAmount(user)` and `allocations[user]`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
