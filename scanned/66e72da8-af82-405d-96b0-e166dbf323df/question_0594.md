# Q0594: mWomSV.lock - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an unprivileged attacker reach this through `lock(uint256 _amount)` while the attacker's slot matured one block ago, and drive `totalAmount` out of agreement with `IERC20(mWOM).balanceOf(address(this))` - breaking the invariant that a user must not lose vested value merely because they redeemed late - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker's slot matured one block ago.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and the block in which the mWOM lock is credited) under the attacker's slot matured one block ago, asserting on every row that a user must not lose vested value merely because they redeemed late.
