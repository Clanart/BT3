# Q2281: Airdrop.claim - bonus scaling loses precision through a fixed 1e9 factor

## Question
In rewards/Airdrop.sol, getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Does `claim()` let an unprivileged caller exploit that under the token balance held by the contract is below the sum of remaining claimable amounts, so that `totalEndRemainingAllocation` diverges from `totalRemainingAllocation`, the invariant that a pro-rata bonus must not silently round small participants down to nothing is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: bonus scaling loses precision through a fixed 1e9 factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Precondition: the token balance held by the contract is below the sum of remaining claimable amounts.
- Invariant to test: a pro-rata bonus must not silently round small participants down to nothing; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that the token balance held by the contract is below the sum of remaining claimable amounts, fuzz the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation), and assert after every call that a pro-rata bonus must not silently round small participants down to nothing.
