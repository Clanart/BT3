# Q1527: ArbWomUp2.incentiveDeposit - the bull bonus is applied on top of an already-capped reward

## Question
In wombat/ArbWomUp2.sol, the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` while the router pair for the bull swap holds thin liquidity, and drive `bullBonusRatio` out of agreement with `DENOMINATOR` - breaking the invariant that a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure - for Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull bonus is applied on top of an already-capped reward)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. Precondition: the router pair for the bull swap holds thin liquidity.
- Invariant to test: a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure; concretely, `bullBonusRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the router pair for the bull swap holds thin liquidity, call `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, and assert `bullBonusRatio` equals `DENOMINATOR` and that no account can withdraw more than it put in.
