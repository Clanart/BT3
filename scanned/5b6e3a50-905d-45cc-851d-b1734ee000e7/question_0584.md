# Q0584: ArbWomUp2.incentiveDeposit - the bull bonus is applied on top of an already-capped reward

## Question
wombat/ArbWomUp2.sol: the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the contract's BUSD balance is below the tier reward earned, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `calDoubledCounted(account)` and `rewardTier and rewardMultiplier walk` no longer reconcile, violating the invariant that a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull bonus is applied on top of an already-capped reward)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. Precondition: the contract's BUSD balance is below the tier reward earned.
- Invariant to test: a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure; concretely, `calDoubledCounted(account)` must stay reconciled with `rewardTier and rewardMultiplier walk`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens) under the contract's BUSD balance is below the tier reward earned, asserting on every row that a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure.
