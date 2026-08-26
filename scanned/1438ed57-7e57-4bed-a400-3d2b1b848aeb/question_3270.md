# Q3270: mWomSV.startUnlock - getUserTotalLocked underflow bricks the position

## Question
Consider wombat/mWomSV.sol, where getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. Assuming the mWOM balance of the locker is exactly equal to totalAmount before the action, can an unprivileged attacker turn this into a divergence between `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that the locked-balance accessor must never revert, and a user must always be able to read and exit and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflow bricks the position)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: the locked-balance accessor must never revert, and a user must always be able to read and exit; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the mWOM balance of the locker is exactly equal to totalAmount before the action, then assert `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` end identical in both runs.
