# Q0687: mWomSV.lockFor - matured slot decays the rewardable percent toward zero

## Question
Note that in wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an attacker holding only tokens bought on market reach it via `lockFor(uint256 _amount, address _for)` under the attacker's slot matured one block ago and force `getRewardablePercentWAD(user)` apart from `_calExpireForfeit in mWOMSVBaseRewarder`, breaking the invariant that a user must not lose vested value merely because they redeemed late for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker's slot matured one block ago.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker's slot matured one block ago, call `lockFor(uint256 _amount, address _for)`, and assert `getRewardablePercentWAD(user)` equals `_calExpireForfeit in mWOMSVBaseRewarder` and that no account can withdraw more than it put in.
