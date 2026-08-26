# Q0739: ArbWomUp2.incentiveDeposit - calDoubledCounted is read from mutable balance state

## Question
wombat/ArbWomUp2.sol: the double-count correction is derived from a live balance read rather than from a stored per-user record of what was already rewarded, so any action that lowers that balance lowers the correction and re-opens the same tier. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the contract's BUSD balance is below the tier reward earned, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `calDoubledCounted(account)` and `rewardTier and rewardMultiplier walk` no longer reconcile, violating the invariant that a double-count correction must be a monotonic stored record, not a live balance read and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: calDoubledCounted is read from mutable balance state)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the double-count correction is derived from a live balance read rather than from a stored per-user record of what was already rewarded, so any action that lowers that balance lowers the correction and re-opens the same tier. Precondition: the contract's BUSD balance is below the tier reward earned.
- Invariant to test: a double-count correction must be a monotonic stored record, not a live balance read; concretely, `calDoubledCounted(account)` must stay reconciled with `rewardTier and rewardMultiplier walk`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the contract's BUSD balance is below the tier reward earned, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, then assert the victim's claimable value and the `calDoubledCounted(account)` versus `rewardTier and rewardMultiplier walk` relation are unchanged by the attacker's transaction.
