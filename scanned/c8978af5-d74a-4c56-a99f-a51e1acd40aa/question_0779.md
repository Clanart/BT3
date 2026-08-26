# Q0779: VLMGP.startUnlock - claim scheduled inside the cooldown window to erase the forfeit

## Question
In VLMGP.sol, an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Does `startUnlock(uint256 _amountToCoolDown)` let an unprivileged caller exploit that under the attacker's slot matured exactly one second ago, so that `userUnlockings[user][i].endTime` diverges from `block.timestamp`, the invariant that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: claim scheduled inside the cooldown window to erase the forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker's slot matured exactly one second ago, then assert `userUnlockings[user][i].endTime` and `block.timestamp` end identical in both runs.
