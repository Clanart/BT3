# Q2839: ArbWomUp3.incentiveDeposit - the double-count correction is a live balance read that the user can lower

## Question
Note that in wombat/ArbWomUp3.sol, calDoubledCounted() derives the already-rewarded amount purely from mWomSV.getUserTotalLocked(_account), and that figure falls as soon as the user calls mWomSV.startUnlock, so the same tier bracket can be claimed again after moving the balance into cooldown. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller repeats the deposit from several addresses in one block and force `bracketRewarded` apart from `calDoubledCounted(account)`, breaking the invariant that a record of what a user has already been rewarded must be stored monotonically, not recomputed from a balance the user can move for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the double-count correction is a live balance read that the user can lower)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: calDoubledCounted() derives the already-rewarded amount purely from mWomSV.getUserTotalLocked(_account), and that figure falls as soon as the user calls mWomSV.startUnlock, so the same tier bracket can be claimed again after moving the balance into cooldown. Precondition: the caller repeats the deposit from several addresses in one block.
- Invariant to test: a record of what a user has already been rewarded must be stored monotonically, not recomputed from a balance the user can move; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller repeats the deposit from several addresses in one block, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, then assert the victim's claimable value and the `bracketRewarded` versus `calDoubledCounted(account)` relation are unchanged by the attacker's transaction.
