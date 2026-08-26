# Q2843: VLMGP.startUnlock - claim scheduled inside the cooldown window to erase the forfeit

## Question
In VLMGP.sol, an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Does `startUnlock(uint256 _amountToCoolDown)` let an unprivileged caller exploit that under the pool the attacker voted for has since been deactivated so unvote reverts, so that `maxSlot` diverges from `userUnlockings[user].length`, the invariant that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: claim scheduled inside the cooldown window to erase the forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot) under the pool the attacker voted for has since been deactivated so unvote reverts, asserting on every row that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant.
