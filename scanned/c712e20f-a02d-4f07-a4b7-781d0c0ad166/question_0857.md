# Q0857: Airdrop.claim - bonus scaling loses precision through a fixed 1e9 factor

## Question
In rewards/Airdrop.sol, getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Does `claim()` let an unprivileged caller exploit that under exactly one unclaimed allocation remains besides the attacker's, so that `totalBonus` diverges from `aidropToken.balanceOf(address(this))`, the invariant that a pro-rata bonus must not silently round small participants down to nothing is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: bonus scaling loses precision through a fixed 1e9 factor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getBonusAmount() computes ((userAllocation * 10**9) * totalBonus) / totalEndRemainingAllocation / 10**9, so a small allocation against a large denominator truncates to zero while a large one keeps full value. Precondition: exactly one unclaimed allocation remains besides the attacker's.
- Invariant to test: a pro-rata bonus must not silently round small participants down to nothing; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange exactly one unclaimed allocation remains besides the attacker's, call `claim()`, and assert `totalBonus` equals `aidropToken.balanceOf(address(this))` and that no account can withdraw more than it put in.
