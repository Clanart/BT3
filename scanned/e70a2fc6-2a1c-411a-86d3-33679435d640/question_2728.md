# Q2728: mWomSV.unlock - matured slot decays the rewardable percent toward zero

## Question
wombat/mWomSV.sol: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. With _slotIndex and the redemption timing under attacker control and a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, can an unprivileged caller sequence `unlock(uint256 _slotIndex)` so that `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` no longer reconcile, violating the invariant that a user must not lose vested value merely because they redeemed late and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `unlock(uint256 _slotIndex)`: constrain the setup so that a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, fuzz the attacker inputs (_slotIndex and the redemption timing), and assert after every call that a user must not lose vested value merely because they redeemed late.
