# Q3652: VLMGP.unlock - forceUnLock skips the settlement that unlock performs

## Question
In VLMGP.sol, unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Can an unprivileged attacker reach this through `unlock(uint256 _slotIndex)` while the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, and drive `getRewardablePercentWAD(user)` out of agreement with `userUnlockings[user][i].amountInCoolDown` - breaking the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock - for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and how long after endTime the slot is redeemed) under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, asserting on every row that every exit path must settle vesting accrual under identical rules before reducing the lock.
