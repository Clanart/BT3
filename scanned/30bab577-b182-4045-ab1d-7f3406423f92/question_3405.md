# Q3405: VLMGP.cancelUnlock - cancelUnlock is not nonReentrant while every sibling is

## Question
In VLMGP.sol, lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Does `cancelUnlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, so that `userInfos[user].factor in ReferralStorage` diverges from `getUserTotalLocked(user)`, the invariant that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is not nonReentrant while every sibling is)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the moment the cooldown is aborted) under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, asserting on every row that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain.
