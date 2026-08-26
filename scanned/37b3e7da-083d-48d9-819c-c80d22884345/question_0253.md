# Q0253: mWomSV.startUnlock - slot reuse resets the cooldown clock

## Question
In wombat/mWomSV.sol, getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Starting from a state where the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged EOA use `startUnlock(uint256 _amountToCoolDown)` to leave `getUserAmountInCoolDown(user)` inconsistent with `totalAmountInCoolDown`, violating the invariant that cooldown already served must not be transferable to a newly committed amount and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: slot reuse resets the cooldown clock)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getNextAvailableUnlockSlot() hands back the first index with amountInCoolDown == 0 once the array reaches maxSlot, and startUnlock overwrites that slot's startTime and endTime, so committed cooldown time can be recycled. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: cooldown already served must not be transferable to a newly committed amount; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `getUserAmountInCoolDown(user)` versus `totalAmountInCoolDown` relation are unchanged by the attacker's transaction.
