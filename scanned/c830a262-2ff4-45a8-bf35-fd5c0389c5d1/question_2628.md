# Q2628: mWomSV.startUnlock - slot reuse resets the cooldown clock

## Question
Consider wombat/mWomSV.sol, where getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Assuming a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, can an unprivileged attacker turn this into a divergence between `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that cooldown already served must not be transferable to a newly committed amount and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse resets the cooldown clock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: cooldown already served must not be transferable to a newly committed amount; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `getUserTotalLocked(user)` versus `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` relation are unchanged by the attacker's transaction.
