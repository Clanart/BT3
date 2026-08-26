# Q1599: ReferralStorage.updateTotalFactor - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, is there an unprivileged sequence of `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` that leaves `refererPercentage + refereePercentage` unreconciled with `DENOMINATOR`, violates the invariant that a boost weight must not reward splitting one position across addresses, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, then assert `refererPercentage + refereePercentage` and `DENOMINATOR` end identical in both runs.
