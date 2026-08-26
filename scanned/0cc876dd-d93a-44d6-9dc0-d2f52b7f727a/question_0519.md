# Q0519: MGPRelease.claim - claim proceeds when claimable is zero

## Question
In rewards/MGPRelease.sol, claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Can an unprivileged attacker reach this through `claim()` while block.timestamp is exactly endTimestamp, and drive `startTimestamp and endTimestamp` out of agreement with `block.timestamp` - breaking the invariant that a claim that moves no value must revert rather than emit - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim proceeds when claimable is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Precondition: block.timestamp is exactly endTimestamp.
- Invariant to test: a claim that moves no value must revert rather than emit; concretely, `startTimestamp and endTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under block.timestamp is exactly endTimestamp, asserting at the end that `startTimestamp and endTimestamp` still equals `block.timestamp` and the PoC's balance delta is non-positive.
