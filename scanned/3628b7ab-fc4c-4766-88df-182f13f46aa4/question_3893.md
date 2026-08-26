# Q3893: mWomSV.startUnlock - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Does `startUnlock(uint256 _amountToCoolDown)` let an unprivileged caller exploit that under the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, so that `getUserTotalLocked(user)` diverges from `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`, the invariant that a user must not lose vested value merely because they redeemed late is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `startUnlock(uint256 _amountToCoolDown)`: constrain the setup so that the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, fuzz the attacker inputs (_amountToCoolDown and the timestamps written into the slot), and assert after every call that a user must not lose vested value merely because they redeemed late.
