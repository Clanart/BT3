# Q2028: Airdrop.claim - bonus scaling loses precision through a fixed 1e9 factor

## Question
In rewards/Airdrop.sol, getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Can an unprivileged attacker reach this through `claim()` while the first honest claim transaction is pending in the mempool, and drive `sum of all allocations` out of agreement with `aidropToken.balanceOf(address(this))` - breaking the invariant that a pro-rata bonus must not silently round small participants down to nothing - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: bonus scaling loses precision through a fixed 1e9 factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Precondition: the first honest claim transaction is pending in the mempool.
- Invariant to test: a pro-rata bonus must not silently round small participants down to nothing; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the first honest claim transaction is pending in the mempool, snapshot `sum of all allocations` and `aidropToken.balanceOf(address(this))`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
