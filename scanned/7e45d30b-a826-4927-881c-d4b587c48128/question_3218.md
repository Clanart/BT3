# Q3218: VLMGP.startUnlock - claim scheduled inside the cooldown window to erase the forfeit

## Question
VLMGP.sol - an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Can an unprivileged attacker controlling _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot, under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, exploit this through `startUnlock(uint256 _amountToCoolDown)` to break the reconciliation between `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and the invariant that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant, yielding High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: claim scheduled inside the cooldown window to erase the forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `startUnlock(uint256 _amountToCoolDown)` sequence atomically under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, asserting at the end that `getUserTotalLocked(user)` still equals `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and the PoC's balance delta is non-positive.
