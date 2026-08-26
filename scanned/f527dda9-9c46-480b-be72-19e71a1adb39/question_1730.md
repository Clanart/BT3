# Q1730: ArbWomUp2.incentiveDeposit - the bull bonus is applied on top of an already-capped reward

## Question
In wombat/ArbWomUp2.sol, the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. Starting from a state where userWOMDeposited is still zero for the caller, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` to leave `calDoubledCounted(account)` inconsistent with `rewardTier and rewardMultiplier walk`, violating the invariant that a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull bonus is applied on top of an already-capped reward)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. Precondition: userWOMDeposited is still zero for the caller.
- Invariant to test: a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure; concretely, `calDoubledCounted(account)` must stay reconciled with `rewardTier and rewardMultiplier walk`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`: constrain the setup so that userWOMDeposited is still zero for the caller, fuzz the attacker inputs (_amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens), and assert after every call that a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure.
