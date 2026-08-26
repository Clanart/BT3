# Q1230: ArbWomUp2.incentiveDeposit - calDoubledCounted is read from mutable balance state

## Question
wombat/ArbWomUp2.sol - the double-count correction is derived from a live balance read rather than from a stored per-user record of what was already rewarded, so any action that lowers that balance lowers the correction and re-opens the same tier. Can an unprivileged attacker controlling _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens, under the caller splits the deposit across several addresses, exploit this through `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` to break the reconciliation between `rewardToSend` and `IERC20(busd).balanceOf(address(this))` and the invariant that a double-count correction must be a monotonic stored record, not a live balance read, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: calDoubledCounted is read from mutable balance state)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the double-count correction is derived from a live balance read rather than from a stored per-user record of what was already rewarded, so any action that lowers that balance lowers the correction and re-opens the same tier. Precondition: the caller splits the deposit across several addresses.
- Invariant to test: a double-count correction must be a monotonic stored record, not a live balance read; concretely, `rewardToSend` must stay reconciled with `IERC20(busd).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller splits the deposit across several addresses, call `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, and assert `rewardToSend` equals `IERC20(busd).balanceOf(address(this))` and that no account can withdraw more than it put in.
