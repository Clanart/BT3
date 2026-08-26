# Q3047: VLMGP.cancelUnlock - cancelUnlock is not nonReentrant while every sibling is

## Question
VLMGP.sol: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Under the pool the attacker voted for has since been deactivated so unvote reverts, is there an unprivileged sequence of `cancelUnlock(uint256 _slotIndex)` that leaves `totalPenalty` unreconciled with `IERC20(MGP).balanceOf(address(this))`, violates the invariant that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain, and delivers Critical - Direct theft of user funds?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is not nonReentrant while every sibling is)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `cancelUnlock(uint256 _slotIndex)` sequence atomically under the pool the attacker voted for has since been deactivated so unvote reverts, asserting at the end that `totalPenalty` still equals `IERC20(MGP).balanceOf(address(this))` and the PoC's balance delta is non-positive.
