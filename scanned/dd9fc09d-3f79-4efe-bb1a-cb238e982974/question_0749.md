# Q0749: mWomSV.startUnlock - forfeit erased by settling inside the cooldown

## Question
In wombat/mWomSV.sol, an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Can an unprivileged attacker reach this through `startUnlock(uint256 _amountToCoolDown)` while the attacker's slot matured one block ago, and drive `mWomSV.getUserTotalLocked(user)` out of agreement with `ArbWomUp3.calDoubledCounted(user)` - breaking the invariant that vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forfeit erased by settling inside the cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Precondition: the attacker's slot matured one block ago.
- Invariant to test: vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `startUnlock(uint256 _amountToCoolDown)`: constrain the setup so that the attacker's slot matured one block ago, fuzz the attacker inputs (_amountToCoolDown and the timestamps written into the slot), and assert after every call that vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks.
