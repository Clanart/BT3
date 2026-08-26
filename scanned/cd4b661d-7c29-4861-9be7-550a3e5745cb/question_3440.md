# Q3440: mWomSV.cancelUnlock - cancelUnlock is the only slot mutator without nonReentrant

## Question
In wombat/mWomSV.sol, lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Starting from a state where the mWOM balance of the locker is exactly equal to totalAmount before the action, can an unprivileged EOA use `cancelUnlock(uint256 _slotIndex)` to leave `getUserAmountInCoolDown(user)` inconsistent with `totalAmountInCoolDown`, violating the invariant that all slot-mutating functions must share a single reentrancy domain and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is the only slot mutator without nonReentrant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock and unlock all carry nonReentrant while cancelUnlock() does not, so it remains callable from inside an external call that another path opened. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: all slot-mutating functions must share a single reentrancy domain; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the mWOM balance of the locker is exactly equal to totalAmount before the action, then assert `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown` end identical in both runs.
