# Q0082: Airdrop.claim - re-snapshotting after other claims collapses the denominator

## Question
In rewards/Airdrop.sol, each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Starting from a state where block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, can an unprivileged EOA use `claim()` to leave `getBonusAmount(user)` inconsistent with `allocations[user]`, violating the invariant that a participant must not be able to shrink the denominator that scales their own bonus and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: re-snapshotting after other claims collapses the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Precondition: block.timestamp has just passed periodsEndTime[4] and no one has claimed yet.
- Invariant to test: a participant must not be able to shrink the denominator that scales their own bonus; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, have the attacker run `claim()`, then assert the victim's claimable value and the `getBonusAmount(user)` versus `allocations[user]` relation are unchanged by the attacker's transaction.
