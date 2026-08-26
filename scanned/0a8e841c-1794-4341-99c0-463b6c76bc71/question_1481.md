# Q1481: VLMGP.startUnlock - getUserTotalLocked underflows and bricks every read

## Question
Consider VLMGP.sol, where getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Assuming coolDownInSecs is at its configured production value and endTime is far in the future, can an unprivileged attacker turn this into a divergence between `getRewardablePercentWAD(user)` and `userUnlockings[user][i].amountInCoolDown` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position and producing Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflows and bricks every read)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot) under coolDownInSecs is at its configured production value and endTime is far in the future, asserting on every row that the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position.
