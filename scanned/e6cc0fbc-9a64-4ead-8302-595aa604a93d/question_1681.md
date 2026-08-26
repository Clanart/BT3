# Q1681: ArbWomUp3.incentiveDeposit - the incentive pot is drainable by one oversized deposit

## Question
In wombat/ArbWomUp3.sol, the tier walk has no per-transaction ceiling, and with the mode two doubling the payout can reach and exceed the whole MGP balance in one call. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` while the caller sets _convertRatio to zero so the whole leg is swapped, and drive `mWomSV.getUserTotalLocked(account) read by getRewardAmount` out of agreement with `the same read inside calDoubledCounted` - breaking the invariant that an incentive pot must not be fully claimable by a single actor in one transaction - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the incentive pot is drainable by one oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: the tier walk has no per-transaction ceiling, and with the mode two doubling the payout can reach and exceed the whole MGP balance in one call. Precondition: the caller sets _convertRatio to zero so the whole leg is swapped.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `mWomSV.getUserTotalLocked(account) read by getRewardAmount` must stay reconciled with `the same read inside calDoubledCounted`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _convertRatio to zero so the whole leg is swapped, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, then assert the victim's claimable value and the `mWomSV.getUserTotalLocked(account) read by getRewardAmount` versus `the same read inside calDoubledCounted` relation are unchanged by the attacker's transaction.
