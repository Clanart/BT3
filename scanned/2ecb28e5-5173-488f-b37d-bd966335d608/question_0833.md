# Q0833: ArbWomUp3.incentiveDeposit - the reward is computed against a pre-deposit balance while the deposit is credited first

## Question
Consider wombat/ArbWomUp3.sol, where incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender, _mode == 2) before calling _deposit, but _deposit mode 2 locks into mWomSV, so the tier input, the double-count correction and the resulting locked balance are three different views of one state. Assuming the caller sets _mode to a value other than 1 or 2, can an unprivileged attacker turn this into a divergence between `_convertRatio supplied by the caller` and `the buyback leg inside SmartWomConvert` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that the tier input and the correction that offsets it must be taken from one snapshot and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the reward is computed against a pre-deposit balance while the deposit is credited first)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender, _mode == 2) before calling _deposit, but _deposit mode 2 locks into mWomSV, so the tier input, the double-count correction and the resulting locked balance are three different views of one state. Precondition: the caller sets _mode to a value other than 1 or 2.
- Invariant to test: the tier input and the correction that offsets it must be taken from one snapshot; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the buyback leg inside SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller sets _mode to a value other than 1 or 2, then assert `_convertRatio supplied by the caller` and `the buyback leg inside SmartWomConvert` end identical in both runs.
