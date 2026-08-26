# Q0129: mWomSV.lockFor - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an unprivileged attacker reach this through `lockFor(uint256 _amount, address _for)` while the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, and drive `totalAmount` out of agreement with `IERC20(mWOM).balanceOf(address(this))` - breaking the invariant that a user must not lose vested value merely because they redeemed late - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `lockFor(uint256 _amount, address _for)`: constrain the setup so that the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, fuzz the attacker inputs (_for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3), and assert after every call that a user must not lose vested value merely because they redeemed late.
