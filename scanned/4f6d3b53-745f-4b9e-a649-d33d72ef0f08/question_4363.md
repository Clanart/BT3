# Q4363: VLMGP.cancelUnlock - cancelUnlock is not nonReentrant while every sibling is

## Question
In VLMGP.sol, lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Starting from a state where the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, can an unprivileged EOA use `cancelUnlock(uint256 _slotIndex)` to leave `getUserTotalLocked(user)` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`, violating the invariant that all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain and extracting Critical - Direct theft of user funds?

## Target
- File/function: VLMGP.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: cancelUnlock is not nonReentrant while every sibling is)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: lock, lockFor, startUnlock, unlock and forceUnLock all carry nonReentrant but cancelUnlock() does not, so it is the one slot-mutating path reachable from inside another external call. Precondition: the victim has a large unsettled userRewards balance in vlMGPBaseRewarder.
- Invariant to test: all functions mutating userUnlockings and totalAmountInCoolDown must share one reentrancy domain; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, call `cancelUnlock(uint256 _slotIndex)`, and assert `getUserTotalLocked(user)` equals `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and that no account can withdraw more than it put in.
