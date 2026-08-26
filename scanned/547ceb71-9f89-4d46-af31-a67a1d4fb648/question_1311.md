# Q1311: MGPRelease.claim - the pre-start branch subtracts claimed from the initial tranche

## Question
Note that in rewards/MGPRelease.sol, getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Can an attacker holding only tokens bought on market reach it via `claim()` under the beneficiary was revoked after having already claimed part of the allocation and force `initialUnlockedAmount` apart from `beneficiaries[account].claimed`, breaking the invariant that a vesting accessor must never revert and must never underflow against a previously claimed amount for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the pre-start branch subtracts claimed from the initial tranche)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Precondition: the beneficiary was revoked after having already claimed part of the allocation.
- Invariant to test: a vesting accessor must never revert and must never underflow against a previously claimed amount; concretely, `initialUnlockedAmount` must stay reconciled with `beneficiaries[account].claimed`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the beneficiary was revoked after having already claimed part of the allocation, snapshot `initialUnlockedAmount` and `beneficiaries[account].claimed`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
