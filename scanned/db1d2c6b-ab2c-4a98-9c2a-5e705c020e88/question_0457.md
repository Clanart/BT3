# Q0457: MGPRelease.claim - the pre-start branch subtracts claimed from the initial tranche

## Question
In rewards/MGPRelease.sol, getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Can an unprivileged attacker reach this through `claim()` while block.timestamp is exactly endTimestamp, and drive `vested` out of agreement with `beneficiaries[account].totalAlloced - initialUnlockedAmount` - breaking the invariant that a vesting accessor must never revert and must never underflow against a previously claimed amount - for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the pre-start branch subtracts claimed from the initial tranche)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Precondition: block.timestamp is exactly endTimestamp.
- Invariant to test: a vesting accessor must never revert and must never underflow against a previously claimed amount; concretely, `vested` must stay reconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under block.timestamp is exactly endTimestamp, then assert `vested` and `beneficiaries[account].totalAlloced - initialUnlockedAmount` end identical in both runs.
