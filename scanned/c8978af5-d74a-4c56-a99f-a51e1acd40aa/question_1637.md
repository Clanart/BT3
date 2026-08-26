# Q1637: mWomSV.lock - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Starting from a state where the attacker arrived through SmartWomConvert.convertFor with _mode == 2, can an unprivileged EOA use `lock(uint256 _amount)` to leave `userUnlockings[user][i].amountInCoolDown` inconsistent with `maxSlot`, violating the invariant that a user must not lose vested value merely because they redeemed late and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker arrived through SmartWomConvert.convertFor with _mode == 2.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker arrived through SmartWomConvert.convertFor with _mode == 2, call `lock(uint256 _amount)`, and assert `userUnlockings[user][i].amountInCoolDown` equals `maxSlot` and that no account can withdraw more than it put in.
