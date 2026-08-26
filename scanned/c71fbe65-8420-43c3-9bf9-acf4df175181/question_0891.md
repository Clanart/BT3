# Q0891: MGPRelease.claim - the pre-start branch subtracts claimed from the initial tranche

## Question
Consider rewards/MGPRelease.sol, where getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Assuming the contract balance is below the sum of unclaimed allocations, can an unprivileged attacker turn this into a divergence between `startTimestamp and endTimestamp` and `block.timestamp` via `claim()`, breaking the invariant that a vesting accessor must never revert and must never underflow against a previously claimed amount and producing Critical - Permanent freezing of funds?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the pre-start branch subtracts claimed from the initial tranche)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: getClaimable() returns initialUnlockedAmount - vesting.claimed while block.timestamp is at or below startTimestamp, with no floor, so any claimed figure above the initial tranche makes the accessor revert. Precondition: the contract balance is below the sum of unclaimed allocations.
- Invariant to test: a vesting accessor must never revert and must never underflow against a previously claimed amount; concretely, `startTimestamp and endTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the contract balance is below the sum of unclaimed allocations, have the attacker run `claim()`, then assert the victim's claimable value and the `startTimestamp and endTimestamp` versus `block.timestamp` relation are unchanged by the attacker's transaction.
