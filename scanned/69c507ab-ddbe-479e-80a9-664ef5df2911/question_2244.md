# Q2244: mWomSV.startUnlock - matured slot decays the rewardable percent toward zero

## Question
wombat/mWomSV.sol - for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an unprivileged attacker controlling _amountToCoolDown and the timestamps written into the slot, under the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, exploit this through `startUnlock(uint256 _amountToCoolDown)` to break the reconciliation between `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown` and the invariant that a user must not lose vested value merely because they redeemed late, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, snapshot `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown`, run the attacker's `startUnlock(uint256 _amountToCoolDown)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
