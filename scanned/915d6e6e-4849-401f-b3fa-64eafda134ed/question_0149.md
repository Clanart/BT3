# Q0149: ArbWomUp.incentiveDeposit - the tier walk underflows at the bottom bracket

## Question
In wombat/ArbWomUp.sol, getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount)` while the contract's USDT balance is below the tier reward the deposit earned, and drive `accumulated = _amount + userWOMDeposited[account]` out of agreement with `the tier boundary crossed` - breaking the invariant that a tier accessor must handle every accumulation value without reverting - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier walk underflows at the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Precondition: the contract's USDT balance is below the tier reward the deposit earned.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `accumulated = _amount + userWOMDeposited[account]` must stay reconciled with `the tier boundary crossed`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the contract's USDT balance is below the tier reward the deposit earned, have the attacker run `incentiveDeposit(uint256 _amount)`, then assert the victim's claimable value and the `accumulated = _amount + userWOMDeposited[account]` versus `the tier boundary crossed` relation are unchanged by the attacker's transaction.
