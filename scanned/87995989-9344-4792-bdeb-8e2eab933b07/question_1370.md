# Q1370: ArbWomUp3.incentiveDeposit - the incentive pot is drainable by one oversized deposit

## Question
wombat/ArbWomUp3.sol: the tier walk has no per-transaction ceiling, and with the mode two doubling the payout can reach and exceed the whole MGP balance in one call. With _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer under attacker control and the MGP balance is below twice the capped reward, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` so that `IERC20(mWom).balanceOf(address(this))` and `the amount locked for _account in mode two` no longer reconcile, violating the invariant that an incentive pot must not be fully claimable by a single actor in one transaction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the incentive pot is drainable by one oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the tier walk has no per-transaction ceiling, and with the mode two doubling the payout can reach and exceed the whole MGP balance in one call. Precondition: the MGP balance is below twice the capped reward.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `IERC20(mWom).balanceOf(address(this))` must stay reconciled with `the amount locked for _account in mode two`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the MGP balance is below twice the capped reward, call `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, and assert `IERC20(mWom).balanceOf(address(this))` equals `the amount locked for _account in mode two` and that no account can withdraw more than it put in.
