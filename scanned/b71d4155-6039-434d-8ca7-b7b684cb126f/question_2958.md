# Q2958: mWomSV.startUnlock - getUserTotalLocked underflow bricks the position

## Question
Consider wombat/mWomSV.sol, where getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. Assuming the attacker holds a second address so lockFor can be used across two accounts, can an unprivileged attacker turn this into a divergence between `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that the locked-balance accessor must never revert, and a user must always be able to read and exit and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflow bricks the position)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: the locked-balance accessor must never revert, and a user must always be able to read and exit; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker holds a second address so lockFor can be used across two accounts, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `mWomSV.getUserTotalLocked(user)` versus `ArbWomUp3.calDoubledCounted(user)` relation are unchanged by the attacker's transaction.
