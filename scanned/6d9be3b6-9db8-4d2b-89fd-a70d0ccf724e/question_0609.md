# Q0609: Airdrop.claim - period accumulation reads a mutable schedule array

## Question
In rewards/Airdrop.sol, getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Can an unprivileged attacker reach this through `claim()` while most participants have already claimed so totalRemainingAllocation is small, and drive `getClaimableAmount(user)` out of agreement with `allocations[user]` - breaking the invariant that a vesting accumulation must not lose value to a single trailing truncation across several periods - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: period accumulation reads a mutable schedule array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Precondition: most participants have already claimed so totalRemainingAllocation is small.
- Invariant to test: a vesting accumulation must not lose value to a single trailing truncation across several periods; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up most participants have already claimed so totalRemainingAllocation is small, snapshot `getClaimableAmount(user)` and `allocations[user]`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
