# Q2787: mWomSV.cancelUnlock - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Does `cancelUnlock(uint256 _slotIndex)` let an unprivileged caller exploit that under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, so that `userUnlockings[user][i].amountInCoolDown` diverges from `maxSlot`, the invariant that a user must not lose vested value merely because they redeemed late is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, have the attacker run `cancelUnlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `userUnlockings[user][i].amountInCoolDown` versus `maxSlot` relation are unchanged by the attacker's transaction.
