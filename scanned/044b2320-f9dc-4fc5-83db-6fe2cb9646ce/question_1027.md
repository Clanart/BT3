# Q1027: VLMGP.unlock - matured slot left unredeemed decays the rewardable percent for everyone

## Question
Consider VLMGP.sol, where for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Assuming the attacker's slot matured exactly one second ago, can an unprivileged attacker turn this into a divergence between `maxSlot` and `userUnlockings[user].length` via `unlock(uint256 _slotIndex)`, breaking the invariant that value must not be confiscated purely because a user delayed a redemption they were entitled to make and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot left unredeemed decays the rewardable percent for everyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: value must not be confiscated purely because a user delayed a redemption they were entitled to make; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `unlock(uint256 _slotIndex)` sequence atomically under the attacker's slot matured exactly one second ago, asserting at the end that `maxSlot` still equals `userUnlockings[user].length` and the PoC's balance delta is non-positive.
