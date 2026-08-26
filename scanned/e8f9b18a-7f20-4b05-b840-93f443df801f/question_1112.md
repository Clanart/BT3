# Q1112: ArbWomUp3.incentiveDeposit - bracketRewarded exists but is not the basis of the correction

## Question
Consider wombat/ArbWomUp3.sol, where the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Assuming the caller sets _mode to a value other than 1 or 2, can an unprivileged attacker turn this into a divergence between `rewardToSend after the _mode == 2 doubling` and `the mgpleft cap applied inside getRewardAmount` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that the stored record of what has been rewarded must be the single basis of the correction and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: bracketRewarded exists but is not the basis of the correction)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Precondition: the caller sets _mode to a value other than 1 or 2.
- Invariant to test: the stored record of what has been rewarded must be the single basis of the correction; concretely, `rewardToSend after the _mode == 2 doubling` must stay reconciled with `the mgpleft cap applied inside getRewardAmount`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence atomically under the caller sets _mode to a value other than 1 or 2, asserting at the end that `rewardToSend after the _mode == 2 doubling` still equals `the mgpleft cap applied inside getRewardAmount` and the PoC's balance delta is non-positive.
