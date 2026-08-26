# Q0191: mWomSV.startUnlock - forfeit erased by settling inside the cooldown

## Question
Consider wombat/mWomSV.sol, where an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Assuming the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged attacker turn this into a divergence between `userUnlockings[user][i].amountInCoolDown` and `maxSlot` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forfeit erased by settling inside the cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, then assert `userUnlockings[user][i].amountInCoolDown` and `maxSlot` end identical in both runs.
