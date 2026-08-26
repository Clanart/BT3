# Q1314: ArbWomUp2.incentiveDeposit - the bull bonus is applied on top of an already-capped reward

## Question
In wombat/ArbWomUp2.sol, the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. Starting from a state where the caller crosses several tier boundaries in one deposit, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` to leave `_minMGPRec supplied by the caller` inconsistent with `the MGP actually received by the swap`, violating the invariant that a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull bonus is applied on top of an already-capped reward)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the reward figure is capped at the contract's remaining balance inside getRewardAmount and the bull path then applies bullBonusRatio on top of it, so the bonus is computed against a figure that was already truncated by a balance shortfall. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: a bonus multiplier must apply to the earned entitlement, not to a balance-truncated figure; concretely, `_minMGPRec supplied by the caller` must stay reconciled with `the MGP actually received by the swap`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence atomically under the caller crosses several tier boundaries in one deposit, asserting at the end that `_minMGPRec supplied by the caller` still equals `the MGP actually received by the swap` and the PoC's balance delta is non-positive.
