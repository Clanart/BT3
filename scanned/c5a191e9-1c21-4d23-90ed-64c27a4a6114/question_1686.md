# Q1686: VLMGP.cancelUnlock - cancelUnlock is not nonReentrant while every sibling is

## Question
Note that in VLMGP.sol, lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Can an attacker holding only tokens bought on market reach it via `cancelUnlock(uint256 _slotIndex)` under coolDownInSecs is at its configured production value and endTime is far in the future and force `totalAmount` apart from `sum of userInfo[vlmgp][*].amount in MasterMagpie`, breaking the invariant that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain for Critical - Direct theft of user funds?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is not nonReentrant while every sibling is)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish coolDownInSecs is at its configured production value and endTime is far in the future, have the attacker run `cancelUnlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `totalAmount` versus `sum of userInfo[vlmgp][*].amount in MasterMagpie` relation are unchanged by the attacker's transaction.
