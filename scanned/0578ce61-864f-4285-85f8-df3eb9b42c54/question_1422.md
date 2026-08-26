# Q1422: ArbWomUp.incentiveDeposit - the tier walk underflows at the bottom bracket

## Question
wombat/ArbWomUp.sol: getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. With _amount with no per-user or global cap, and how many times the call is repeated under attacker control and the caller has already claimed most of their tier entitlement, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount)` so that `claimedReward[account]` and `userWOMDeposited[account]` no longer reconcile, violating the invariant that a tier accessor must handle every accumulation value without reverting and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier walk underflows at the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Precondition: the caller has already claimed most of their tier entitlement.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller has already claimed most of their tier entitlement, then assert `claimedReward[account]` and `userWOMDeposited[account]` end identical in both runs.
