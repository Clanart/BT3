# Q0764: Airdrop.claim - re-snapshotting after other claims collapses the denominator

## Question
In rewards/Airdrop.sol, each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Does `claim()` let an unprivileged caller exploit that under exactly one unclaimed allocation remains besides the attacker's, so that `periodsEndTime[4]` diverges from `block.timestamp`, the invariant that a participant must not be able to shrink the denominator that scales their own bonus is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: re-snapshotting after other claims collapses the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Precondition: exactly one unclaimed allocation remains besides the attacker's.
- Invariant to test: a participant must not be able to shrink the denominator that scales their own bonus; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under exactly one unclaimed allocation remains besides the attacker's, then assert `periodsEndTime[4]` and `block.timestamp` end identical in both runs.
