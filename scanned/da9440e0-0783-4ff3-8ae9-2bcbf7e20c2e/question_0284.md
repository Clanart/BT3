# Q0284: mWomSV.startUnlock - matured slot decays the rewardable percent toward zero

## Question
wombat/mWomSV.sol - for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an unprivileged attacker controlling _amountToCoolDown and the timestamps written into the slot, under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, exploit this through `startUnlock(uint256 _amountToCoolDown)` to break the reconciliation between `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` and the invariant that a user must not lose vested value merely because they redeemed late, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, then assert `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` end identical in both runs.
