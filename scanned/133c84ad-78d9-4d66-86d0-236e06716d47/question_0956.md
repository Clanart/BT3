# Q0956: ArbWomUp2.incentiveDeposit - the incentive pot is drainable by one oversized deposit

## Question
wombat/ArbWomUp2.sol: the tier walk has no per-transaction ceiling and the only bound is the contract's balance, so a single caller can take the entire remaining incentive. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and bullBonusRatio is configured well above zero, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `calDoubledCounted(account)` and `rewardTier and rewardMultiplier walk` no longer reconcile, violating the invariant that an incentive pot must not be fully claimable by a single actor in one transaction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the incentive pot is drainable by one oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the tier walk has no per-transaction ceiling and the only bound is the contract's balance, so a single caller can take the entire remaining incentive. Precondition: bullBonusRatio is configured well above zero.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `calDoubledCounted(account)` must stay reconciled with `rewardTier and rewardMultiplier walk`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish bullBonusRatio is configured well above zero, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, then assert the victim's claimable value and the `calDoubledCounted(account)` versus `rewardTier and rewardMultiplier walk` relation are unchanged by the attacker's transaction.
