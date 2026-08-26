# Q2350: Airdrop.claim - period accumulation reads a mutable schedule array

## Question
In rewards/Airdrop.sol, getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Does `claim()` let an unprivileged caller exploit that under the token balance held by the contract is below the sum of remaining claimable amounts, so that `getClaimableAmount(user)` diverges from `allocations[user]`, the invariant that a vesting accumulation must not lose value to a single trailing truncation across several periods is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: period accumulation reads a mutable schedule array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Precondition: the token balance held by the contract is below the sum of remaining claimable amounts.
- Invariant to test: a vesting accumulation must not lose value to a single trailing truncation across several periods; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under the token balance held by the contract is below the sum of remaining claimable amounts, asserting at the end that `getClaimableAmount(user)` still equals `allocations[user]` and the PoC's balance delta is non-positive.
