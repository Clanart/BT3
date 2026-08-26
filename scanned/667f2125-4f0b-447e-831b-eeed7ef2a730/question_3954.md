# Q3954: VLMGP.startUnlock - getUserTotalLocked underflows and bricks every read

## Question
In VLMGP.sol, getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Starting from a state where a large vesting MGP distribution has just been queued into the vlMGP rewarder, can an unprivileged EOA use `startUnlock(uint256 _amountToCoolDown)` to leave `getUserTotalLocked(user)` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`, violating the invariant that the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position and extracting Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflows and bricks every read)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up a large vesting MGP distribution has just been queued into the vlMGP rewarder, snapshot `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`, run the attacker's `startUnlock(uint256 _amountToCoolDown)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
