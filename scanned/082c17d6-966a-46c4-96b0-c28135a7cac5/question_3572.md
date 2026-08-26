# Q3572: VLMGP.startUnlock - claim scheduled inside the cooldown window to erase the forfeit

## Question
Note that in VLMGP.sol, an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor and force `getUserAmountInCoolDown(user)` apart from `totalAmountInCoolDown`, breaking the invariant that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: claim scheduled inside the cooldown window to erase the forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `startUnlock(uint256 _amountToCoolDown)`: constrain the setup so that the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, fuzz the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot), and assert after every call that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant.
