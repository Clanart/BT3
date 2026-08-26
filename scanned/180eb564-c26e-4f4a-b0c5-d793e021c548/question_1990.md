# Q1990: VLMGP.startUnlock - getUserTotalLocked underflows and bricks every read

## Question
VLMGP.sol: getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, is there an unprivileged sequence of `startUnlock(uint256 _amountToCoolDown)` that leaves `userUnlockings[user][i].endTime` unreconciled with `block.timestamp`, violates the invariant that the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflows and bricks every read)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `userUnlockings[user][i].endTime` versus `block.timestamp` relation are unchanged by the attacker's transaction.
