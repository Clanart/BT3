# Q1676: Airdrop.updateEndRemainingAllocation - front-running the first claim to fix the snapshot

## Question
rewards/Airdrop.sol - because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Can an unprivileged attacker controlling the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times, under the attacker's allocation is the largest remaining one, exploit this through `updateEndRemainingAllocation()` to break the reconciliation between `getBonusAmount(user)` and `allocations[user]` and the invariant that the snapshot must not be influenceable by transaction ordering, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: the attacker's allocation is the largest remaining one.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `updateEndRemainingAllocation()`: constrain the setup so that the attacker's allocation is the largest remaining one, fuzz the attacker inputs (the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times), and assert after every call that the snapshot must not be influenceable by transaction ordering.
