# Q2588: mWomSV.startUnlock - forfeit erased by settling inside the cooldown

## Question
wombat/mWomSV.sol: an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, is there an unprivileged sequence of `startUnlock(uint256 _amountToCoolDown)` that leaves `getRewardablePercentWAD(user)` unreconciled with `_calExpireForfeit in mWOMSVBaseRewarder`, violates the invariant that vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forfeit erased by settling inside the cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `startUnlock(uint256 _amountToCoolDown)` sequence atomically under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, asserting at the end that `getRewardablePercentWAD(user)` still equals `_calExpireForfeit in mWOMSVBaseRewarder` and the PoC's balance delta is non-positive.
