# Q3354: VLMGP.unlock - matured slot left unredeemed decays the rewardable percent for everyone

## Question
VLMGP.sol: for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. With _slotIndex and how long after endTime the slot is redeemed under attacker control and the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, can an unprivileged caller sequence `unlock(uint256 _slotIndex)` so that `userUnlockings[user][i].endTime` and `block.timestamp` no longer reconcile, violating the invariant that value must not be confiscated purely because a user delayed a redemption they were entitled to make and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot left unredeemed decays the rewardable percent for everyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: value must not be confiscated purely because a user delayed a redemption they were entitled to make; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, have the attacker run `unlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `userUnlockings[user][i].endTime` versus `block.timestamp` relation are unchanged by the attacker's transaction.
