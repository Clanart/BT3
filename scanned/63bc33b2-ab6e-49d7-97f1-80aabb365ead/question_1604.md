# Q1604: ArbWomUp.incentiveDeposit - the tier walk underflows at the bottom bracket

## Question
wombat/ArbWomUp.sol: getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Under the USDT implementation returns false rather than reverting on failure, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount)` that leaves `rewardAmount / DENOMINATOR` unreconciled with `claimedReward[account]`, violates the invariant that a tier accessor must handle every accumulation value without reverting, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier walk underflows at the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Precondition: the USDT implementation returns false rather than reverting on failure.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount)` sequence atomically under the USDT implementation returns false rather than reverting on failure, asserting at the end that `rewardAmount / DENOMINATOR` still equals `claimedReward[account]` and the PoC's balance delta is non-positive.
