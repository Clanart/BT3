# Q0275: ArbWomUp3.incentiveDeposit - the incentive pot is drainable by one oversized deposit

## Question
Consider wombat/ArbWomUp3.sol, where the tier walk has no per-transaction ceiling, and with the mode two doubling the payout can reach and exceed the whole MGP balance in one call. Assuming the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, can an unprivileged attacker turn this into a divergence between `rewardToSend` and `IERC20(mgp).balanceOf(address(this))` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that an incentive pot must not be fully claimable by a single actor in one transaction and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the incentive pot is drainable by one oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the tier walk has no per-transaction ceiling, and with the mode two doubling the payout can reach and exceed the whole MGP balance in one call. Precondition: the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`: constrain the setup so that the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, fuzz the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer), and assert after every call that an incentive pot must not be fully claimable by a single actor in one transaction.
