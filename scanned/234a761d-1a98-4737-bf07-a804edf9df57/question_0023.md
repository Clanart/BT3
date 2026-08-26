# Q0023: MGPRelease.claim - the pre-start branch subtracts claimed from the initial tranche

## Question
Note that in rewards/MGPRelease.sol, getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Can an attacker holding only tokens bought on market reach it via `claim()` under block.timestamp is below startTimestamp and the initial tranche has already been claimed and force `beneficiaries[account].claimed` apart from `getClaimable(account)`, breaking the invariant that a vesting accessor must never revert and must never underflow against a previously claimed amount for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the pre-start branch subtracts claimed from the initial tranche)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Precondition: block.timestamp is below startTimestamp and the initial tranche has already been claimed.
- Invariant to test: a vesting accessor must never revert and must never underflow against a previously claimed amount; concretely, `beneficiaries[account].claimed` must stay reconciled with `getClaimable(account)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange block.timestamp is below startTimestamp and the initial tranche has already been claimed, call `claim()`, and assert `beneficiaries[account].claimed` equals `getClaimable(account)` and that no account can withdraw more than it put in.
