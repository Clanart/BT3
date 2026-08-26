# Q2603: ArbWomUp3.incentiveDeposit - the double-count correction is a live balance read that the user can lower

## Question
Consider wombat/ArbWomUp3.sol, where calDoubledCounted() derives the already-rewarded amount purely from mWomSV.getUserTotalLocked(_account), and that figure falls as soon as the user calls mWomSV.startUnlock, so the same tier bracket can be claimed again after moving the balance into cooldown. Assuming the caller crosses several tier boundaries in one deposit, can an unprivileged attacker turn this into a divergence between `rewardToSend` and `IERC20(mgp).balanceOf(address(this))` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that a record of what a user has already been rewarded must be stored monotonically, not recomputed from a balance the user can move and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the double-count correction is a live balance read that the user can lower)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: calDoubledCounted() derives the already-rewarded amount purely from mWomSV.getUserTotalLocked(_account), and that figure falls as soon as the user calls mWomSV.startUnlock, so the same tier bracket can be claimed again after moving the balance into cooldown. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: a record of what a user has already been rewarded must be stored monotonically, not recomputed from a balance the user can move; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer) under the caller crosses several tier boundaries in one deposit, asserting on every row that a record of what a user has already been rewarded must be stored monotonically, not recomputed from a balance the user can move.
