# Q2129: mWomSV.lockFor - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an unprivileged attacker reach this through `lockFor(uint256 _amount, address _for)` while the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, and drive `getUserTotalLocked(user)` out of agreement with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` - breaking the invariant that a user must not lose vested value merely because they redeemed late - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `lockFor(uint256 _amount, address _for)` sequence atomically under the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, asserting at the end that `getUserTotalLocked(user)` still equals `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` and the PoC's balance delta is non-positive.
