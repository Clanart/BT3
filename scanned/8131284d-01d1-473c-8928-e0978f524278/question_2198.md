# Q2198: mWomSV.startUnlock - getUserTotalLocked underflow bricks the position

## Question
Consider wombat/mWomSV.sol, where getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. Assuming the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, can an unprivileged attacker turn this into a divergence between `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that the locked-balance accessor must never revert, and a user must always be able to read and exit and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflow bricks the position)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: the locked-balance accessor must never revert, and a user must always be able to read and exit; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamps written into the slot) under the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, asserting on every row that the locked-balance accessor must never revert, and a user must always be able to read and exit.
