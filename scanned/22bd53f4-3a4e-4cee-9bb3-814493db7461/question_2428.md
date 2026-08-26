# Q2428: mWomSV.cancelUnlock - cancelUnlock is the only slot mutator without nonReentrant

## Question
wombat/mWomSV.sol: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Under the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, is there an unprivileged sequence of `cancelUnlock(uint256 _slotIndex)` that leaves `userUnlockings[user][i].amountInCoolDown` unreconciled with `maxSlot`, violates the invariant that all slot-mutating functions must share a single reentrancy domain, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is the only slot mutator without nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: all slot-mutating functions must share a single reentrancy domain; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, then assert `userUnlockings[user][i].amountInCoolDown` and `maxSlot` end identical in both runs.
