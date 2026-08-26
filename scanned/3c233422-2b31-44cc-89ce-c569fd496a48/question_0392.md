# Q0392: Airdrop.updateEndRemainingAllocation - front-running the first claim to fix the snapshot

## Question
In rewards/Airdrop.sol, because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Can an unprivileged attacker reach this through `updateEndRemainingAllocation()` while most participants have already claimed so totalRemainingAllocation is small, and drive `periodsEndTime[4]` out of agreement with `block.timestamp` - breaking the invariant that the snapshot must not be influenceable by transaction ordering - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: most participants have already claimed so totalRemainingAllocation is small.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `updateEndRemainingAllocation()`: constrain the setup so that most participants have already claimed so totalRemainingAllocation is small, fuzz the attacker inputs (the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times), and assert after every call that the snapshot must not be influenceable by transaction ordering.
