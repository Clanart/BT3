# Q1059: mWomSV.cancelUnlock - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Does `cancelUnlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the attacker's slot matured one block ago, so that `getUserTotalLocked(user)` diverges from `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`, the invariant that a user must not lose vested value merely because they redeemed late is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker's slot matured one block ago.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `cancelUnlock(uint256 _slotIndex)`: constrain the setup so that the attacker's slot matured one block ago, fuzz the attacker inputs (_slotIndex and the moment the cooldown is aborted), and assert after every call that a user must not lose vested value merely because they redeemed late.
