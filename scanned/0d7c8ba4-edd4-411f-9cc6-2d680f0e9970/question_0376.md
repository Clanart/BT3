# Q0376: VLMGP.unlock - matured slot left unredeemed decays the rewardable percent for everyone

## Question
VLMGP.sol: for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, is there an unprivileged sequence of `unlock(uint256 _slotIndex)` that leaves `userTotalVotedInVlmgp(user) in WombatBribeManager` unreconciled with `getUserTotalLocked(user)`, violates the invariant that value must not be confiscated purely because a user delayed a redemption they were entitled to make, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot left unredeemed decays the rewardable percent for everyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: value must not be confiscated purely because a user delayed a redemption they were entitled to make; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, then assert `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` end identical in both runs.
