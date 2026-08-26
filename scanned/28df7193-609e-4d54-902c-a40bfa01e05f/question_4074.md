# Q4074: VLMGP.cancelUnlock - cancelUnlock is not nonReentrant while every sibling is

## Question
Consider VLMGP.sol, where lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Assuming a large vesting MGP distribution has just been queued into the vlMGP rewarder, can an unprivileged attacker turn this into a divergence between `maxSlot` and `userUnlockings[user].length` via `cancelUnlock(uint256 _slotIndex)`, breaking the invariant that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain and producing Critical - Direct theft of user funds?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is not nonReentrant while every sibling is)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish a large vesting MGP distribution has just been queued into the vlMGP rewarder, have the attacker run `cancelUnlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `maxSlot` versus `userUnlockings[user].length` relation are unchanged by the attacker's transaction.
