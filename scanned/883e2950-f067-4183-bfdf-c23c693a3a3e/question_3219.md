# Q3219: mWomSV.lockFor - matured slot decays the rewardable percent toward zero

## Question
Consider wombat/mWomSV.sol, where for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Assuming the mWOM balance of the locker is exactly equal to totalAmount before the action, can an unprivileged attacker turn this into a divergence between `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` via `lockFor(uint256 _amount, address _for)`, breaking the invariant that a user must not lose vested value merely because they redeemed late and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the mWOM balance of the locker is exactly equal to totalAmount before the action, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `getRewardablePercentWAD(user)` versus `_calExpireForfeit in mWOMSVBaseRewarder` relation are unchanged by the attacker's transaction.
