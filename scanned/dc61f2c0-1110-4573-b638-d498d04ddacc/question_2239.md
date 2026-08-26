# Q2239: ArbWomUp3.incentiveDeposit - the incentive pot is drainable by one oversized deposit

## Question
Note that in wombat/ArbWomUp3.sol, the tier walk has no per-transaction ceiling, and with the mode two doubling the payout can reach and exceed the whole MGP balance in one call. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under a residual mWOM balance from an earlier call sits on the contract and force `rewardToSend` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that an incentive pot must not be fully claimable by a single actor in one transaction for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the incentive pot is drainable by one oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the tier walk has no per-transaction ceiling, and with the mode two doubling the payout can reach and exceed the whole MGP balance in one call. Precondition: a residual mWOM balance from an earlier call sits on the contract.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish a residual mWOM balance from an earlier call sits on the contract, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, then assert the victim's claimable value and the `rewardToSend` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
