# Q1726: Airdrop.claim - the bonus numerator grows as others forfeit

## Question
In rewards/Airdrop.sol, claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Can an unprivileged attacker reach this through `claim()` while the attacker's allocation is the largest remaining one, and drive `getBonusAmount(user)` out of agreement with `allocations[user]` - breaking the invariant that a bonus share must be fixed against a snapshot taken before any participant could influence it - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: the bonus numerator grows as others forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Precondition: the attacker's allocation is the largest remaining one.
- Invariant to test: a bonus share must be fixed against a snapshot taken before any participant could influence it; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker's allocation is the largest remaining one, then assert `getBonusAmount(user)` and `allocations[user]` end identical in both runs.
