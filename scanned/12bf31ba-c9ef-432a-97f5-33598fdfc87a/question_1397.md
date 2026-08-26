# Q1397: ArbWomUp3.incentiveDeposit - the tier walk underflows below the bottom bracket

## Question
In wombat/ArbWomUp3.sol, both getRewardAmount and calDoubledCounted end with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever the accumulation sits below rewardTier[0]. Does `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` let an unprivileged caller exploit that under the MGP balance is below twice the capped reward, so that `mWomSV.getUserTotalLocked(account) read by getRewardAmount` diverges from `the same read inside calDoubledCounted`, the invariant that a tier accessor must handle every accumulation value without reverting is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the tier walk underflows below the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: both getRewardAmount and calDoubledCounted end with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever the accumulation sits below rewardTier[0]. Precondition: the MGP balance is below twice the capped reward.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `mWomSV.getUserTotalLocked(account) read by getRewardAmount` must stay reconciled with `the same read inside calDoubledCounted`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the MGP balance is below twice the capped reward, call `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, and assert `mWomSV.getUserTotalLocked(account) read by getRewardAmount` equals `the same read inside calDoubledCounted` and that no account can withdraw more than it put in.
