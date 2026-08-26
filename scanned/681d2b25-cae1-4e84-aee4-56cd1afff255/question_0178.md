# Q0178: MGPRelease.claim - the schedule boundaries are read live on every claim

## Question
Note that in rewards/MGPRelease.sol, getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Can an attacker holding only tokens bought on market reach it via `claim()` under block.timestamp is below startTimestamp and the initial tranche has already been claimed and force `beneficiaries[account].claimed` apart from `getClaimable(account)`, breaking the invariant that a vesting schedule must not be able to move under a beneficiary who has already claimed against it for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the schedule boundaries are read live on every claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() branches on block.timestamp against startTimestamp and endTimestamp on every call, so any change to those boundaries retroactively rewrites the vested figure against an already-advanced claimed counter. Precondition: block.timestamp is below startTimestamp and the initial tranche has already been claimed.
- Invariant to test: a vesting schedule must not be able to move under a beneficiary who has already claimed against it; concretely, `beneficiaries[account].claimed` must stay reconciled with `getClaimable(account)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up block.timestamp is below startTimestamp and the initial tranche has already been claimed, snapshot `beneficiaries[account].claimed` and `getClaimable(account)`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
