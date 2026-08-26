# Q4749: VLMGP.startUnlock - claim scheduled inside the cooldown window to erase the forfeit

## Question
VLMGP.sol: an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Under the attacker repeats cancelUnlock and startUnlock inside a single transaction, is there an unprivileged sequence of `startUnlock(uint256 _amountToCoolDown)` that leaves `totalPenalty` unreconciled with `IERC20(MGP).balanceOf(address(this))`, violates the invariant that the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: claim scheduled inside the cooldown window to erase the forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: an attacker calls startUnlock and then settles every vesting reward through MasterMagpie.multiclaimSpec while the slot is still cooling down, because getRewardablePercentWAD only starts decaying after endTime has passed. Precondition: the attacker repeats cancelUnlock and startUnlock inside a single transaction.
- Invariant to test: the forfeit applied to a vesting reward must reflect the lock state the reward was earned under, not the state at an attacker-chosen settlement instant; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker repeats cancelUnlock and startUnlock inside a single transaction, snapshot `totalPenalty` and `IERC20(MGP).balanceOf(address(this))`, run the attacker's `startUnlock(uint256 _amountToCoolDown)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
