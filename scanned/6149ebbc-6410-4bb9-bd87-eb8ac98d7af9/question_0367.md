# Q0367: ArbWomUp2.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
wombat/ArbWomUp2.sol: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the caller sets _bullMode to false so the plain transfer branch runs, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `calDoubledCounted(account)` and `rewardTier and rewardMultiplier walk` no longer reconcile, violating the invariant that the tier input and the deposit record must be derived from one snapshot and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. Precondition: the caller sets _bullMode to false so the plain transfer branch runs.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `calDoubledCounted(account)` must stay reconciled with `rewardTier and rewardMultiplier walk`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens) under the caller sets _bullMode to false so the plain transfer branch runs, asserting on every row that the tier input and the deposit record must be derived from one snapshot.
