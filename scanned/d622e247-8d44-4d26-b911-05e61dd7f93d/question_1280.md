# Q1280: Airdrop.claim - period accumulation reads a mutable schedule array

## Question
rewards/Airdrop.sol: getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Under totalBonus has grown large from earlier forfeits, is there an unprivileged sequence of `claim()` that leaves `sum of all allocations` unreconciled with `aidropToken.balanceOf(address(this))`, violates the invariant that a vesting accumulation must not lose value to a single trailing truncation across several periods, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: period accumulation reads a mutable schedule array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: a vesting accumulation must not lose value to a single trailing truncation across several periods; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange totalBonus has grown large from earlier forfeits, call `claim()`, and assert `sum of all allocations` equals `aidropToken.balanceOf(address(this))` and that no account can withdraw more than it put in.
