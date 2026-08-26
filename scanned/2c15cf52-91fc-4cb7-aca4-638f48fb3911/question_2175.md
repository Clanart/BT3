# Q2175: mWomSV.startUnlock - forfeit erased by settling inside the cooldown

## Question
Consider wombat/mWomSV.sol, where an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Assuming the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, can an unprivileged attacker turn this into a divergence between `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: forfeit erased by settling inside the cooldown)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: an attacker calls startUnlock and immediately settles the mWOMSV rewarder through MasterMagpie.multiclaimSpec, capturing the full vesting amount before getRewardablePercentWAD begins to decay after endTime. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: vesting forfeit must be a function of the lock commitment actually served, not of settlement timing the user picks; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, then assert `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` end identical in both runs.
