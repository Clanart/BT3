# Q0454: Airdrop.claim - the bonus numerator grows as others forfeit

## Question
In rewards/Airdrop.sol, claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Can an unprivileged attacker reach this through `claim()` while most participants have already claimed so totalRemainingAllocation is small, and drive `periodsEndTime[4]` out of agreement with `block.timestamp` - breaking the invariant that a bonus share must be fixed against a snapshot taken before any participant could influence it - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: the bonus numerator grows as others forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Precondition: most participants have already claimed so totalRemainingAllocation is small.
- Invariant to test: a bonus share must be fixed against a snapshot taken before any participant could influence it; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange most participants have already claimed so totalRemainingAllocation is small, call `claim()`, and assert `periodsEndTime[4]` equals `block.timestamp` and that no account can withdraw more than it put in.
