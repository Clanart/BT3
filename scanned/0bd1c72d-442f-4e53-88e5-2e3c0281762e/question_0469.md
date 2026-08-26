# Q0469: VLMGP.cancelUnlock - cancelUnlock is not nonReentrant while every sibling is

## Question
In VLMGP.sol, lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Does `cancelUnlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, so that `getUserTotalLocked(user)` diverges from `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`, the invariant that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is not nonReentrant while every sibling is)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `cancelUnlock(uint256 _slotIndex)` sequence atomically under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, asserting at the end that `getUserTotalLocked(user)` still equals `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and the PoC's balance delta is non-positive.
