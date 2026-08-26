# Q4134: VLMGP.forceUnLock - expectedPenaltyAmount reads msg.sender rather than the slot owner

## Question
In VLMGP.sol, expectedPenaltyAmount(uint256) is public and indexes userUnlockings[msg.sender][_slotIndex], so the penalty quoted to any integrating contract is the caller's own slot rather than the slot being force-unlocked. Can an unprivileged attacker reach this through `forceUnLock(uint256 _slotIndex)` while a large vesting MGP distribution has just been queued into the vlMGP rewarder, and drive `getUserTotalLocked(user)` out of agreement with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` - breaking the invariant that a pricing helper used to settle a position must be parameterised by that position's owner - for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: expectedPenaltyAmount reads msg.sender rather than the slot owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: expectedPenaltyAmount(uint256) is public and indexes userUnlockings[msg.sender][_slotIndex], so the penalty quoted to any integrating contract is the caller's own slot rather than the slot being force-unlocked. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: a pricing helper used to settle a position must be parameterised by that position's owner; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `forceUnLock(uint256 _slotIndex)` sequence atomically under a large vesting MGP distribution has just been queued into the vlMGP rewarder, asserting at the end that `getUserTotalLocked(user)` still equals `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and the PoC's balance delta is non-positive.
