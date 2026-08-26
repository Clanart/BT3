# Q0501: mWomSV.cancelUnlock - matured slot decays the rewardable percent toward zero

## Question
wombat/mWomSV.sol: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, is there an unprivileged sequence of `cancelUnlock(uint256 _slotIndex)` that leaves `mWomSV.getUserTotalLocked(user)` unreconciled with `ArbWomUp3.calDoubledCounted(user)`, violates the invariant that a user must not lose vested value merely because they redeemed late, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, then assert `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)` end identical in both runs.
