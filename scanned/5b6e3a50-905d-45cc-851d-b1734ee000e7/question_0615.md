# Q0615: ArbWomUp2.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
wombat/ArbWomUp2.sol: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the contract's BUSD balance is below the tier reward earned, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `claimedReward[account]` and `userWOMDeposited[account]` no longer reconcile, violating the invariant that the tier input and the deposit record must be derived from one snapshot and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. Precondition: the contract's BUSD balance is below the tier reward earned.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract's BUSD balance is below the tier reward earned, then assert `claimedReward[account]` and `userWOMDeposited[account]` end identical in both runs.
