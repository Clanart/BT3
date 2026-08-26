# Q0088: ArbWomUp2.incentiveDeposit - the bull bonus is applied on top of an already-capped reward

## Question
wombat/ArbWomUp2.sol: the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the caller sets _minMGPRec to zero and sandwiches the router pair, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `_minMGPRec supplied by the caller` and `the MGP actually received by the swap` no longer reconcile, violating the invariant that a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull bonus is applied on top of an already-capped reward)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. Precondition: the caller sets _minMGPRec to zero and sandwiches the router pair.
- Invariant to test: a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure; concretely, `_minMGPRec supplied by the caller` must stay reconciled with `the MGP actually received by the swap`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _minMGPRec to zero and sandwiches the router pair, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, then assert the victim's claimable value and the `_minMGPRec supplied by the caller` versus `the MGP actually received by the swap` relation are unchanged by the attacker's transaction.
