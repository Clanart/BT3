# Q1844: Airdrop.claim - period accumulation reads a mutable schedule array

## Question
In rewards/Airdrop.sol, getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Starting from a state where the attacker's allocation is the largest remaining one, can an unprivileged EOA use `claim()` to leave `totalBonus` inconsistent with `aidropToken.balanceOf(address(this))`, violating the invariant that a vesting accumulation must not lose value to a single trailing truncation across several periods and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: period accumulation reads a mutable schedule array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Precondition: the attacker's allocation is the largest remaining one.
- Invariant to test: a vesting accumulation must not lose value to a single trailing truncation across several periods; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker's allocation is the largest remaining one, snapshot `totalBonus` and `aidropToken.balanceOf(address(this))`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
