# Q2647: VLMGP.cancelUnlock - cancelUnlock is not nonReentrant while every sibling is

## Question
VLMGP.sol: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Under the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, is there an unprivileged sequence of `cancelUnlock(uint256 _slotIndex)` that leaves `userUnlockings[user][i].endTime` unreconciled with `block.timestamp`, violates the invariant that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain, and delivers Critical - Direct theft of user funds?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is not nonReentrant while every sibling is)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `cancelUnlock(uint256 _slotIndex)`: constrain the setup so that the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, fuzz the attacker inputs (_slotIndex and the moment the cooldown is aborted), and assert after every call that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain.
