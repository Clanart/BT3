# Q2336: mWomSV.unlock - matured slot decays the rewardable percent toward zero

## Question
wombat/mWomSV.sol: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. With _slotIndex and the redemption timing under attacker control and the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, can an unprivileged caller sequence `unlock(uint256 _slotIndex)` so that `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` no longer reconcile, violating the invariant that a user must not lose vested value merely because they redeemed late and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, call `unlock(uint256 _slotIndex)`, and assert `totalAmount` equals `IERC20(mWOM).balanceOf(address(this))` and that no account can withdraw more than it put in.
