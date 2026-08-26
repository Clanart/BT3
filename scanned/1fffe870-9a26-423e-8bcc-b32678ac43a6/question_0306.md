# Q0306: ArbWomUp3.incentiveDeposit - the tier walk underflows below the bottom bracket

## Question
Consider wombat/ArbWomUp3.sol, where both getRewardAmount and calDoubledCounted end with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever the accumulation sits below rewardTier[0]. Assuming the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, can an unprivileged attacker turn this into a divergence between `bracketRewarded` and `calDoubledCounted(account)` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that a tier accessor must handle every accumulation value without reverting and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the tier walk underflows below the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: both getRewardAmount and calDoubledCounted end with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever the accumulation sits below rewardTier[0]. Precondition: the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer) under the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, asserting on every row that a tier accessor must handle every accumulation value without reverting.
