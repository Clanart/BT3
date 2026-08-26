# Q2583: ArbWomUp3.incentiveDeposit - bracketRewarded exists but is not the basis of the correction

## Question
wombat/ArbWomUp3.sol: the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. With _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer under attacker control and the caller's mWomSV locked balance is zero so the correction is at its bottom bracket, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` so that `mWomSV.getUserTotalLocked(account) read by getRewardAmount` and `the same read inside calDoubledCounted` no longer reconcile, violating the invariant that the stored record of what has been rewarded must be the single basis of the correction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: bracketRewarded exists but is not the basis of the correction)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the contract keeps a bracketRewarded record while calDoubledCounted derives the correction from the live mWomSV balance instead, so the stored record and the applied correction can disagree. Precondition: the caller's mWomSV locked balance is zero so the correction is at its bottom bracket.
- Invariant to test: the stored record of what has been rewarded must be the single basis of the correction; concretely, `mWomSV.getUserTotalLocked(account) read by getRewardAmount` must stay reconciled with `the same read inside calDoubledCounted`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller's mWomSV locked balance is zero so the correction is at its bottom bracket, then assert `mWomSV.getUserTotalLocked(account) read by getRewardAmount` and `the same read inside calDoubledCounted` end identical in both runs.
