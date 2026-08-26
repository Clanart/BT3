# Q0863: ArbWomUp2.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
wombat/ArbWomUp2.sol: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and bullBonusRatio is configured well above zero, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `rewardToSend` and `IERC20(busd).balanceOf(address(this))` no longer reconcile, violating the invariant that the tier input and the deposit record must be derived from one snapshot and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. Precondition: bullBonusRatio is configured well above zero.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `rewardToSend` must stay reconciled with `IERC20(busd).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up bullBonusRatio is configured well above zero, snapshot `rewardToSend` and `IERC20(busd).balanceOf(address(this))`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
