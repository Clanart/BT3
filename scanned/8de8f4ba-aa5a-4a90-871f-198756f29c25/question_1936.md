# Q1936: Airdrop.updateEndRemainingAllocation - front-running the first claim to fix the snapshot

## Question
In rewards/Airdrop.sol, because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Can an unprivileged attacker reach this through `updateEndRemainingAllocation()` while the first honest claim transaction is pending in the mempool, and drive `getClaimableAmount(user)` out of agreement with `allocations[user]` - breaking the invariant that the snapshot must not be influenceable by transaction ordering - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `updateEndRemainingAllocation()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateEndRemainingAllocation()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the bonus denominator is snapshotted, callable by anyone any number of times
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: the first honest claim transaction is pending in the mempool.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `updateEndRemainingAllocation()` sequence atomically under the first honest claim transaction is pending in the mempool, asserting at the end that `getClaimableAmount(user)` still equals `allocations[user]` and the PoC's balance delta is non-positive.
