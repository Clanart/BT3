# Q2587: VLMGP.unlock - matured slot left unredeemed decays the rewardable percent for everyone

## Question
Consider VLMGP.sol, where for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Assuming the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, can an unprivileged attacker turn this into a divergence between `totalAmount` and `sum of userInfo[vlmgp][*].amount in MasterMagpie` via `unlock(uint256 _slotIndex)`, breaking the invariant that value must not be confiscated purely because a user delayed a redemption they were entitled to make and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot left unredeemed decays the rewardable percent for everyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: value must not be confiscated purely because a user delayed a redemption they were entitled to make; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and how long after endTime the slot is redeemed) under the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, asserting on every row that value must not be confiscated purely because a user delayed a redemption they were entitled to make.
