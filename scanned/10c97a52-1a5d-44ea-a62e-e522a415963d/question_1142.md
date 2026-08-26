# Q1142: ArbWomUp3.incentiveDeposit - the double-count correction is a live balance read that the user can lower

## Question
In wombat/ArbWomUp3.sol, calDoubledCounted() derives the already-rewarded amount purely from mWomSV.getUserTotalLocked(_account), and that figure falls as soon as the user calls mWomSV.startUnlock, so the same tier bracket can be claimed again after moving the balance into cooldown. Does `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` let an unprivileged caller exploit that under the MGP balance is below twice the capped reward, so that `bracketRewarded` diverges from `calDoubledCounted(account)`, the invariant that a record of what a user has already been rewarded must be stored monotonically, not recomputed from a balance the user can move is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the double-count correction is a live balance read that the user can lower)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: calDoubledCounted() derives the already-rewarded amount purely from mWomSV.getUserTotalLocked(_account), and that figure falls as soon as the user calls mWomSV.startUnlock, so the same tier bracket can be claimed again after moving the balance into cooldown. Precondition: the MGP balance is below twice the capped reward.
- Invariant to test: a record of what a user has already been rewarded must be stored monotonically, not recomputed from a balance the user can move; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the MGP balance is below twice the capped reward, then assert `bracketRewarded` and `calDoubledCounted(account)` end identical in both runs.
