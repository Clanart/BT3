# Q1012: Airdrop.claim - front-running the first claim to fix the snapshot

## Question
In rewards/Airdrop.sol, because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Does `claim()` let an unprivileged caller exploit that under exactly one unclaimed allocation remains besides the attacker's, so that `totalEndRemainingAllocation` diverges from `totalRemainingAllocation`, the invariant that the snapshot must not be influenceable by transaction ordering is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: exactly one unclaimed allocation remains besides the attacker's.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up exactly one unclaimed allocation remains besides the attacker's, snapshot `totalEndRemainingAllocation` and `totalRemainingAllocation`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
