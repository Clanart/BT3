# Q3845: mWomSV.startUnlock - forfeit erased by settling inside the cooldown

## Question
In wombat/mWomSV.sol, an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Can an unprivileged attacker reach this through `startUnlock(uint256 _amountToCoolDown)` while the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, and drive `getUserAmountInCoolDown(user)` out of agreement with `totalAmountInCoolDown` - breaking the invariant that vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forfeit erased by settling inside the cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `getUserAmountInCoolDown(user)` versus `totalAmountInCoolDown` relation are unchanged by the attacker's transaction.
