# Q1655: ArbWomUp2.incentiveDeposit - calDoubledCounted is read from mutable balance state

## Question
In wombat/ArbWomUp2.sol, the double-count correction is derived from a live balance read rather than from a stored per-user record of what was already rewarded, so any action that lowers that balance lowers the correction and re-opens the same tier. Does `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` let an unprivileged caller exploit that under the router pair for the bull swap holds thin liquidity, so that `bullBonusRatio` diverges from `DENOMINATOR`, the invariant that a double-count correction must be a monotonic stored record, not a live balance read is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: calDoubledCounted is read from mutable balance state)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the double-count correction is derived from a live balance read rather than from a stored per-user record of what was already rewarded, so any action that lowers that balance lowers the correction and re-opens the same tier. Precondition: the router pair for the bull swap holds thin liquidity.
- Invariant to test: a double-count correction must be a monotonic stored record, not a live balance read; concretely, `bullBonusRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the router pair for the bull swap holds thin liquidity, call `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, and assert `bullBonusRatio` equals `DENOMINATOR` and that no account can withdraw more than it put in.
