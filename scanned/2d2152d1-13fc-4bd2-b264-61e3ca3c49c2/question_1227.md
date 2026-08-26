# Q1227: MGPRelease.claim - the linear term truncates against a large denominator

## Question
Note that in rewards/MGPRelease.sol, vested is (block.timestamp - startTimestamp) * needVesting / (endTimestamp - startTimestamp), so a claim placed early in a long schedule truncates and the truncated remainder is only recovered if the beneficiary claims again later. Can an attacker holding only tokens bought on market reach it via `claim()` under initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation and force `startTimestamp and endTimestamp` apart from `block.timestamp`, breaking the invariant that a linear release must not lose value to truncation across repeated claims for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: the linear term truncates against a large denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: vested is (block.timestamp - startTimestamp) * needVesting / (endTimestamp - startTimestamp), so a claim placed early in a long schedule truncates and the truncated remainder is only recovered if the beneficiary claims again later. Precondition: initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation.
- Invariant to test: a linear release must not lose value to truncation across repeated claims; concretely, `startTimestamp and endTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, then assert `startTimestamp and endTimestamp` and `block.timestamp` end identical in both runs.
