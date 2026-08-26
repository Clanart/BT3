# Q1496: Airdrop.claim - bonus scaling loses precision through a fixed 1e9 factor

## Question
rewards/Airdrop.sol - getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Can an unprivileged attacker controlling the ordering of the claim against every other claimant and against updateEndRemainingAllocation, under the attacker's allocation is small relative to the original totalRemainingAllocation, exploit this through `claim()` to break the reconciliation between `getClaimableAmount(user)` and `allocations[user]` and the invariant that a pro-rata bonus must not silently round small participants down to nothing, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: bonus scaling loses precision through a fixed 1e9 factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Precondition: the attacker's allocation is small relative to the original totalRemainingAllocation.
- Invariant to test: a pro-rata bonus must not silently round small participants down to nothing; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker's allocation is small relative to the original totalRemainingAllocation, have the attacker run `claim()`, then assert the victim's claimable value and the `getClaimableAmount(user)` versus `allocations[user]` relation are unchanged by the attacker's transaction.
