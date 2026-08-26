# Q1135: Airdrop.claim - the bonus numerator grows as others forfeit

## Question
rewards/Airdrop.sol: claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. With the ordering of the claim against every other claimant and against updateEndRemainingAllocation under attacker control and totalBonus has grown large from earlier forfeits, can an unprivileged caller sequence `claim()` so that `totalEndRemainingAllocation` and `totalRemainingAllocation` no longer reconcile, violating the invariant that a bonus share must be fixed against a snapshot taken before any participant could influence it and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: the bonus numerator grows as others forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: a bonus share must be fixed against a snapshot taken before any participant could influence it; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish totalBonus has grown large from earlier forfeits, have the attacker run `claim()`, then assert the victim's claimable value and the `totalEndRemainingAllocation` versus `totalRemainingAllocation` relation are unchanged by the attacker's transaction.
