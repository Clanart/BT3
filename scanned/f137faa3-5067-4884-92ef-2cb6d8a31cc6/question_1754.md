# Q1754: ArbWomUp2.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
wombat/ArbWomUp2.sol - incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. Can an unprivileged attacker controlling _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens, under userWOMDeposited is still zero for the caller, exploit this through `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` to break the reconciliation between `claimedReward[account]` and `userWOMDeposited[account]` and the invariant that the tier input and the deposit record must be derived from one snapshot, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. Precondition: userWOMDeposited is still zero for the caller.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens) under userWOMDeposited is still zero for the caller, asserting on every row that the tier input and the deposit record must be derived from one snapshot.
