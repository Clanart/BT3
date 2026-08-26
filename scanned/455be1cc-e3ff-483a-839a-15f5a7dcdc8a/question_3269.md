# Q3269: VLMGP.startUnlock - getUserTotalLocked underflows and bricks every read

## Question
Note that in VLMGP.sol, getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard and force `userTotalVotedInVlmgp(user) in WombatBribeManager` apart from `getUserTotalLocked(user)`, breaking the invariant that the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position for Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflows and bricks every read)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot) under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, asserting on every row that the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position.
