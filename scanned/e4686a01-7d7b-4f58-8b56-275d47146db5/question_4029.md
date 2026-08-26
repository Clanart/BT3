# Q4029: VLMGP.unlock - matured slot left unredeemed decays the rewardable percent for everyone

## Question
VLMGP.sol - for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Can an unprivileged attacker controlling _slotIndex and how long after endTime the slot is redeemed, under a large vesting MGP distribution has just been queued into the vlMGP rewarder, exploit this through `unlock(uint256 _slotIndex)` to break the reconciliation between `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)` and the invariant that value must not be confiscated purely because a user delayed a redemption they were entitled to make, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot left unredeemed decays the rewardable percent for everyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: value must not be confiscated purely because a user delayed a redemption they were entitled to make; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large vesting MGP distribution has just been queued into the vlMGP rewarder, then assert `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)` end identical in both runs.
