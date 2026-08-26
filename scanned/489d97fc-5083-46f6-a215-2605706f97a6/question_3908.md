# Q3908: VLMGP.startUnlock - claim scheduled inside the cooldown window to erase the forfeit

## Question
Note that in VLMGP.sol, an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under a large vesting MGP distribution has just been queued into the vlMGP rewarder and force `totalAmount` apart from `sum of userInfo[vlmgp][*].amount in MasterMagpie`, breaking the invariant that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: claim scheduled inside the cooldown window to erase the forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large vesting MGP distribution has just been queued into the vlMGP rewarder, then assert `totalAmount` and `sum of userInfo[vlmgp][*].amount in MasterMagpie` end identical in both runs.
