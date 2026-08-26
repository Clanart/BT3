# Q2900: VLMGP.startUnlock - getUserTotalLocked underflows and bricks every read

## Question
In VLMGP.sol, getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Does `startUnlock(uint256 _amountToCoolDown)` let an unprivileged caller exploit that under the pool the attacker voted for has since been deactivated so unvote reverts, so that `userInfos[user].factor in ReferralStorage` diverges from `getUserTotalLocked(user)`, the invariant that the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflows and bricks every read)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the pool the attacker voted for has since been deactivated so unvote reverts, snapshot `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)`, run the attacker's `startUnlock(uint256 _amountToCoolDown)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
