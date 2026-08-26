# Q2014: mWomSV.cancelUnlock - cancelUnlock is the only slot mutator without nonReentrant

## Question
wombat/mWomSV.sol: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. With _slotIndex and the moment the cooldown is aborted under attacker control and the attacker arrived through SmartWomConvert.convertFor with _mode == 2, can an unprivileged caller sequence `cancelUnlock(uint256 _slotIndex)` so that `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` no longer reconcile, violating the invariant that all slot-mutating functions must share a single reentrancy domain and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is the only slot mutator without nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Precondition: the attacker arrived through SmartWomConvert.convertFor with _mode == 2.
- Invariant to test: all slot-mutating functions must share a single reentrancy domain; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker arrived through SmartWomConvert.convertFor with _mode == 2, call `cancelUnlock(uint256 _slotIndex)`, and assert `getRewardablePercentWAD(user)` equals `_calExpireForfeit in mWOMSVBaseRewarder` and that no account can withdraw more than it put in.
