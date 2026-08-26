# Q1108: MGPRelease.claim - the pre-start branch subtracts claimed from the initial tranche

## Question
In rewards/MGPRelease.sol, getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Starting from a state where initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, can an unprivileged EOA use `claim()` to leave `beneficiaries[account].claimed` inconsistent with `getClaimable(account)`, violating the invariant that a vesting accessor must never revert and must never underflow against a previously claimed amount and extracting Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the pre-start branch subtracts claimed from the initial tranche)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Precondition: initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation.
- Invariant to test: a vesting accessor must never revert and must never underflow against a previously claimed amount; concretely, `beneficiaries[account].claimed` must stay reconciled with `getClaimable(account)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated) under initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, asserting on every row that a vesting accessor must never revert and must never underflow against a previously claimed amount.
