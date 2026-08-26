# Q1574: Airdrop.claim - period accumulation reads a mutable schedule array

## Question
In rewards/Airdrop.sol, getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Does `claim()` let an unprivileged caller exploit that under the attacker's allocation is small relative to the original totalRemainingAllocation, so that `totalEndRemainingAllocation` diverges from `totalRemainingAllocation`, the invariant that a vesting accumulation must not lose value to a single trailing truncation across several periods is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: period accumulation reads a mutable schedule array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Precondition: the attacker's allocation is small relative to the original totalRemainingAllocation.
- Invariant to test: a vesting accumulation must not lose value to a single trailing truncation across several periods; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that the attacker's allocation is small relative to the original totalRemainingAllocation, fuzz the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation), and assert after every call that a vesting accumulation must not lose value to a single trailing truncation across several periods.
