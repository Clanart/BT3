# Q1195: Airdrop.claim - bonus scaling loses precision through a fixed 1e9 factor

## Question
rewards/Airdrop.sol: getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Under totalBonus has grown large from earlier forfeits, is there an unprivileged sequence of `claim()` that leaves `getBonusAmount(user)` unreconciled with `allocations[user]`, violates the invariant that a pro-rata bonus must not silently round small participants down to nothing, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: bonus scaling loses precision through a fixed 1e9 factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: a pro-rata bonus must not silently round small participants down to nothing; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under totalBonus has grown large from earlier forfeits, asserting at the end that `getBonusAmount(user)` still equals `allocations[user]` and the PoC's balance delta is non-positive.
