# Q0268: Airdrop.claim - period accumulation reads a mutable schedule array

## Question
In rewards/Airdrop.sol, getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Starting from a state where block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, can an unprivileged EOA use `claim()` to leave `getBonusAmount(user)` inconsistent with `allocations[user]`, violating the invariant that a vesting accumulation must not lose value to a single trailing truncation across several periods and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: period accumulation reads a mutable schedule array)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: getClaimableAmount() loops over five fixed indexes of periodsEndTime and percentPerPeriod, accumulating userAllocation * percentPerPeriod before a single division by denominator, so the ordering of the five multiplications against one truncation decides the result. Precondition: block.timestamp has just passed periodsEndTime[4] and no one has claimed yet.
- Invariant to test: a vesting accumulation must not lose value to a single trailing truncation across several periods; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, call `claim()`, and assert `getBonusAmount(user)` equals `allocations[user]` and that no account can withdraw more than it put in.
