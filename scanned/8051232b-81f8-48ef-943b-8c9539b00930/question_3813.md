# Q3813: mWomSV.lockFor - matured slot decays the rewardable percent toward zero

## Question
wombat/mWomSV.sol - for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an unprivileged attacker controlling _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3, under the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, exploit this through `lockFor(uint256 _amount, address _for)` to break the reconciliation between `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)` and the invariant that a user must not lose vested value merely because they redeemed late, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3) under the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, asserting on every row that a user must not lose vested value merely because they redeemed late.
