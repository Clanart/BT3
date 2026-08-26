# Q1080: ArbWomUp2.incentiveDeposit - the bull bonus is applied on top of an already-capped reward

## Question
wombat/ArbWomUp2.sol: the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the caller splits the deposit across several addresses, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `rewardToSend` and `IERC20(busd).balanceOf(address(this))` no longer reconcile, violating the invariant that a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull bonus is applied on top of an already-capped reward)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. Precondition: the caller splits the deposit across several addresses.
- Invariant to test: a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure; concretely, `rewardToSend` must stay reconciled with `IERC20(busd).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the caller splits the deposit across several addresses, snapshot `rewardToSend` and `IERC20(busd).balanceOf(address(this))`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
