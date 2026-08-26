# Q4324: VLMGP.unlock - matured slot left unredeemed decays the rewardable percent for everyone

## Question
In VLMGP.sol, for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Starting from a state where the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, can an unprivileged EOA use `unlock(uint256 _slotIndex)` to leave `userTotalVotedInVlmgp(user) in WombatBribeManager` inconsistent with `getUserTotalLocked(user)`, violating the invariant that value must not be confiscated purely because a user delayed a redemption they were entitled to make and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot left unredeemed decays the rewardable percent for everyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Precondition: the victim has a large unsettled userRewards balance in vlMGPBaseRewarder.
- Invariant to test: value must not be confiscated purely because a user delayed a redemption they were entitled to make; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, have the attacker run `unlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `userTotalVotedInVlmgp(user) in WombatBribeManager` versus `getUserTotalLocked(user)` relation are unchanged by the attacker's transaction.
