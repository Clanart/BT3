# Q0113: Airdrop.claim - the bonus numerator grows as others forfeit

## Question
In rewards/Airdrop.sol, claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Starting from a state where block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, can an unprivileged EOA use `claim()` to leave `getClaimableAmount(user)` inconsistent with `allocations[user]`, violating the invariant that a bonus share must be fixed against a snapshot taken before any participant could influence it and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: the bonus numerator grows as others forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Precondition: block.timestamp has just passed periodsEndTime[4] and no one has claimed yet.
- Invariant to test: a bonus share must be fixed against a snapshot taken before any participant could influence it; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, fuzz the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation), and assert after every call that a bonus share must be fixed against a snapshot taken before any participant could influence it.
