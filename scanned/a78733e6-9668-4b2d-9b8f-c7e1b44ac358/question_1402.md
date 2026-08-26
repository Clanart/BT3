# Q1402: VLMGP.startUnlock - claim scheduled inside the cooldown window to erase the forfeit

## Question
VLMGP.sol - an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Can an unprivileged attacker controlling _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot, under coolDownInSecs is at its configured production value and endTime is far in the future, exploit this through `startUnlock(uint256 _amountToCoolDown)` to break the reconciliation between `totalPenalty` and `IERC20(MGP).balanceOf(address(this))` and the invariant that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant, yielding High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: claim scheduled inside the cooldown window to erase the forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `startUnlock(uint256 _amountToCoolDown)` sequence atomically under coolDownInSecs is at its configured production value and endTime is far in the future, asserting at the end that `totalPenalty` still equals `IERC20(MGP).balanceOf(address(this))` and the PoC's balance delta is non-positive.
