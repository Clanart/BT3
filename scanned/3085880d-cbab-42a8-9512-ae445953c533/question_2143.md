# Q2143: Airdrop.claim - front-running the first claim to fix the snapshot

## Question
In rewards/Airdrop.sol, because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Can an unprivileged attacker reach this through `claim()` while the first honest claim transaction is pending in the mempool, and drive `periodsEndTime[4]` out of agreement with `block.timestamp` - breaking the invariant that the snapshot must not be influenceable by transaction ordering - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: the first honest claim transaction is pending in the mempool.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the first honest claim transaction is pending in the mempool, call `claim()`, and assert `periodsEndTime[4]` equals `block.timestamp` and that no account can withdraw more than it put in.
