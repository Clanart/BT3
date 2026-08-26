# Q0036: mWomSV.lock - matured slot decays the rewardable percent toward zero

## Question
Consider wombat/mWomSV.sol, where for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Assuming the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged attacker turn this into a divergence between `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown` via `lock(uint256 _amount)`, breaking the invariant that a user must not lose vested value merely because they redeemed late and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lock(uint256 _amount)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lock(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block in which the mWOM lock is credited
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, snapshot `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown`, run the attacker's `lock(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
