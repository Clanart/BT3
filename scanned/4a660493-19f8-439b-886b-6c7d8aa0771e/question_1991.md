# Q1991: mWomSV.cancelUnlock - matured slot decays the rewardable percent toward zero

## Question
wombat/mWomSV.sol: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. With _slotIndex and the moment the cooldown is aborted under attacker control and the attacker arrived through SmartWomConvert.convertFor with _mode == 2, can an unprivileged caller sequence `cancelUnlock(uint256 _slotIndex)` so that `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` no longer reconcile, violating the invariant that a user must not lose vested value merely because they redeemed late and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `cancelUnlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `cancelUnlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the moment the cooldown is aborted
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker arrived through SmartWomConvert.convertFor with _mode == 2.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker arrived through SmartWomConvert.convertFor with _mode == 2, have the attacker run `cancelUnlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `totalAmount` versus `IERC20(mWOM).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
