# Q2488: Airdrop.claim - the bonus numerator grows as others forfeit

## Question
Consider rewards/Airdrop.sol, where claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Assuming the attacker calls updateEndRemainingAllocation and claim in the same transaction, can an unprivileged attacker turn this into a divergence between `sum of all allocations` and `aidropToken.balanceOf(address(this))` via `claim()`, breaking the invariant that a bonus share must be fixed against a snapshot taken before any participant could influence it and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: the bonus numerator grows as others forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Precondition: the attacker calls updateEndRemainingAllocation and claim in the same transaction.
- Invariant to test: a bonus share must be fixed against a snapshot taken before any participant could influence it; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker calls updateEndRemainingAllocation and claim in the same transaction, have the attacker run `claim()`, then assert the victim's claimable value and the `sum of all allocations` versus `aidropToken.balanceOf(address(this))` relation are unchanged by the attacker's transaction.
