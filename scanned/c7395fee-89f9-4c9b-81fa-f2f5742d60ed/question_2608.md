# Q2608: mWomSV.startUnlock - getUserTotalLocked underflow bricks the position

## Question
wombat/mWomSV.sol - getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. Can an unprivileged attacker controlling _amountToCoolDown and the timestamps written into the slot, under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, exploit this through `startUnlock(uint256 _amountToCoolDown)` to break the reconciliation between `userUnlockings[user][i].amountInCoolDown` and `maxSlot` and the invariant that the locked-balance accessor must never revert, and a user must always be able to read and exit, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflow bricks the position)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: the locked-balance accessor must never revert, and a user must always be able to read and exit; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `startUnlock(uint256 _amountToCoolDown)` sequence atomically under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, asserting at the end that `userUnlockings[user][i].amountInCoolDown` still equals `maxSlot` and the PoC's balance delta is non-positive.
