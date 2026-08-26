# Q1773: ReferralStorage.claimReward - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Starting from a state where the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, can an unprivileged EOA use `claimReward()` to leave `userInfos[account].rewardAmount` inconsistent with `MGP.balanceOf(address(this))`, violating the invariant that a boost weight must not reward splitting one position across addresses and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, then assert `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))` end identical in both runs.
