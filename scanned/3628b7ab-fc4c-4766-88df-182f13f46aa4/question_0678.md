# Q0678: ArbWomUp3.incentiveDeposit - the tier walk underflows below the bottom bracket

## Question
Note that in wombat/ArbWomUp3.sol, both getRewardAmount and calDoubledCounted end with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever the accumulation sits below rewardTier[0]. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller sets _mode to 2 so the doubling applies and force `_convertRatio supplied by the caller` apart from `the buyback leg inside SmartWomConvert`, breaking the invariant that a tier accessor must handle every accumulation value without reverting for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the tier walk underflows below the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: both getRewardAmount and calDoubledCounted end with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever the accumulation sits below rewardTier[0]. Precondition: the caller sets _mode to 2 so the doubling applies.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the buyback leg inside SmartWomConvert`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _mode to 2 so the doubling applies, call `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, and assert `_convertRatio supplied by the caller` equals `the buyback leg inside SmartWomConvert` and that no account can withdraw more than it put in.
