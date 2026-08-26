# Q1294: mWomSV.startUnlock - forfeit erased by settling inside the cooldown

## Question
wombat/mWomSV.sol: an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. With _amountToCoolDown and the timestamps written into the slot under attacker control and the attacker reached maxSlot so slot reuse is forced, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` no longer reconcile, violating the invariant that vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forfeit erased by settling inside the cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Precondition: the attacker reached maxSlot so slot reuse is forced.
- Invariant to test: vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `startUnlock(uint256 _amountToCoolDown)`: constrain the setup so that the attacker reached maxSlot so slot reuse is forced, fuzz the attacker inputs (_amountToCoolDown and the timestamps written into the slot), and assert after every call that vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks.
