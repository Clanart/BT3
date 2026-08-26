# Q3765: mWomSV.lock - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Starting from a state where the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, can an unprivileged EOA use `lock(uint256 _amount)` to leave `userUnlockings[user][i].amountInCoolDown` inconsistent with `maxSlot`, violating the invariant that a user must not lose vested value merely because they redeemed late and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, snapshot `userUnlockings[user][i].amountInCoolDown` and `maxSlot`, run the attacker's `lock(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
