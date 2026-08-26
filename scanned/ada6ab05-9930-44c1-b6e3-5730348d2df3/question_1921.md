# Q1921: VLMGP.startUnlock - claim scheduled inside the cooldown window to erase the forfeit

## Question
In VLMGP.sol, an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Starting from a state where the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, can an unprivileged EOA use `startUnlock(uint256 _amountToCoolDown)` to leave `userInfos[user].factor in ReferralStorage` inconsistent with `getUserTotalLocked(user)`, violating the invariant that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: claim scheduled inside the cooldown window to erase the forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot) under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, asserting on every row that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant.
