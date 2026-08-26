# Q2174: VLMGP.cancelUnlock - cancelUnlock is not nonReentrant while every sibling is

## Question
Note that in VLMGP.sol, lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Can an attacker holding only tokens bought on market reach it via `cancelUnlock(uint256 _slotIndex)` under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one and force `getRewardablePercentWAD(user)` apart from `userUnlockings[user][i].amountInCoolDown`, breaking the invariant that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain for Critical - Direct theft of user funds?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is not nonReentrant while every sibling is)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `cancelUnlock(uint256 _slotIndex)` sequence atomically under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, asserting at the end that `getRewardablePercentWAD(user)` still equals `userUnlockings[user][i].amountInCoolDown` and the PoC's balance delta is non-positive.
