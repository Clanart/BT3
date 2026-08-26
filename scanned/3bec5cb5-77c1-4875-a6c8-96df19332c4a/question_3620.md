# Q3620: VLMGP.startUnlock - getUserTotalLocked underflows and bricks every read

## Question
In VLMGP.sol, getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Does `startUnlock(uint256 _amountToCoolDown)` let an unprivileged caller exploit that under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, so that `maxSlot` diverges from `userUnlockings[user].length`, the invariant that the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflows and bricks every read)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, call `startUnlock(uint256 _amountToCoolDown)`, and assert `maxSlot` equals `userUnlockings[user].length` and that no account can withdraw more than it put in.
