# Q3557: mWomSV.startUnlock - forfeit erased by settling inside the cooldown

## Question
In wombat/mWomSV.sol, an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Can an unprivileged attacker reach this through `startUnlock(uint256 _amountToCoolDown)` while the attacker repeats cancelUnlock and startUnlock inside one transaction, and drive `getUserTotalLocked(user)` out of agreement with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` - breaking the invariant that vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forfeit erased by settling inside the cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker repeats cancelUnlock and startUnlock inside one transaction, then assert `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` end identical in both runs.
