# Q0950: Airdrop.claim - period accumulation reads a mutable schedule array

## Question
In rewards/Airdrop.sol, getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Does `claim()` let an unprivileged caller exploit that under exactly one unclaimed allocation remains besides the attacker's, so that `periodsEndTime[4]` diverges from `block.timestamp`, the invariant that a vesting accumulation must not lose value to a single trailing truncation across several periods is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: period accumulation reads a mutable schedule array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Precondition: exactly one unclaimed allocation remains besides the attacker's.
- Invariant to test: a vesting accumulation must not lose value to a single trailing truncation across several periods; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation) under exactly one unclaimed allocation remains besides the attacker's, asserting on every row that a vesting accumulation must not lose value to a single trailing truncation across several periods.
