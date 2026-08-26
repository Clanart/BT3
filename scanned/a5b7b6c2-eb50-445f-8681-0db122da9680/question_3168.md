# Q3168: mWomSV.lock - matured slot decays the rewardable percent toward zero

## Question
Note that in wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an attacker holding only tokens bought on market reach it via `lock(uint256 _amount)` under the mWOM balance of the locker is exactly equal to totalAmount before the action and force `totalAmount` apart from `IERC20(mWOM).balanceOf(address(this))`, breaking the invariant that a user must not lose vested value merely because they redeemed late for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the mWOM balance of the locker is exactly equal to totalAmount before the action, then assert `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` end identical in both runs.
