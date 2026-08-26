# Q2782: ArbWomUp3.incentiveDeposit - the tier walk underflows below the bottom bracket

## Question
Note that in wombat/ArbWomUp3.sol, both getRewardAmount and calDoubledCounted end with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever the accumulation sits below rewardTier[0]. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller crosses several tier boundaries in one deposit and force `IERC20(mWom).balanceOf(address(this))` apart from `the amount locked for _account in mode two`, breaking the invariant that a tier accessor must handle every accumulation value without reverting for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the tier walk underflows below the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: both getRewardAmount and calDoubledCounted end with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever the accumulation sits below rewardTier[0]. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `IERC20(mWom).balanceOf(address(this))` must stay reconciled with `the amount locked for _account in mode two`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller crosses several tier boundaries in one deposit, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, then assert the victim's claimable value and the `IERC20(mWom).balanceOf(address(this))` versus `the amount locked for _account in mode two` relation are unchanged by the attacker's transaction.
