# Q0175: Airdrop.claim - bonus scaling loses precision through a fixed 1e9 factor

## Question
In rewards/Airdrop.sol, getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Starting from a state where block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, can an unprivileged EOA use `claim()` to leave `sum of all allocations` inconsistent with `aidropToken.balanceOf(address(this))`, violating the invariant that a pro-rata bonus must not silently round small participants down to nothing and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: bonus scaling loses precision through a fixed 1e9 factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Precondition: block.timestamp has just passed periodsEndTime[4] and no one has claimed yet.
- Invariant to test: a pro-rata bonus must not silently round small participants down to nothing; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, then assert `sum of all allocations` and `aidropToken.balanceOf(address(this))` end identical in both runs.
