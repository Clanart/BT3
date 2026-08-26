# Q1105: Airdrop.claim - re-snapshotting after other claims collapses the denominator

## Question
Consider rewards/Airdrop.sol, where each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Assuming totalBonus has grown large from earlier forfeits, can an unprivileged attacker turn this into a divergence between `sum of all allocations` and `aidropToken.balanceOf(address(this))` via `claim()`, breaking the invariant that a participant must not be able to shrink the denominator that scales their own bonus and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: re-snapshotting after other claims collapses the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: a participant must not be able to shrink the denominator that scales their own bonus; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that totalBonus has grown large from earlier forfeits, fuzz the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation), and assert after every call that a participant must not be able to shrink the denominator that scales their own bonus.
