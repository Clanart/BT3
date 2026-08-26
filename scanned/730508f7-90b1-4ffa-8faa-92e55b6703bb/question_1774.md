# Q1774: Airdrop.claim - bonus scaling loses precision through a fixed 1e9 factor

## Question
Consider rewards/Airdrop.sol, where getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Assuming the attacker's allocation is the largest remaining one, can an unprivileged attacker turn this into a divergence between `periodsEndTime[4]` and `block.timestamp` via `claim()`, breaking the invariant that a pro-rata bonus must not silently round small participants down to nothing and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: bonus scaling loses precision through a fixed 1e9 factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Precondition: the attacker's allocation is the largest remaining one.
- Invariant to test: a pro-rata bonus must not silently round small participants down to nothing; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under the attacker's allocation is the largest remaining one, asserting at the end that `periodsEndTime[4]` still equals `block.timestamp` and the PoC's balance delta is non-positive.
