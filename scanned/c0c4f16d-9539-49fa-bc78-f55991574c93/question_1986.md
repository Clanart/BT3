# Q1986: ArbWomUp3.incentiveDeposit - the tier walk underflows below the bottom bracket

## Question
Consider wombat/ArbWomUp3.sol, where both getRewardAmount and calDoubledCounted end with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever the accumulation sits below rewardTier[0]. Assuming the caller sandwiches the wom/mWom Wombat pool around the transaction, can an unprivileged attacker turn this into a divergence between `rewardToSend` and `IERC20(mgp).balanceOf(address(this))` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that a tier accessor must handle every accumulation value without reverting and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the tier walk underflows below the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: both getRewardAmount and calDoubledCounted end with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever the accumulation sits below rewardTier[0]. Precondition: the caller sandwiches the wom/mWom Wombat pool around the transaction.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence atomically under the caller sandwiches the wom/mWom Wombat pool around the transaction, asserting at the end that `rewardToSend` still equals `IERC20(mgp).balanceOf(address(this))` and the PoC's balance delta is non-positive.
