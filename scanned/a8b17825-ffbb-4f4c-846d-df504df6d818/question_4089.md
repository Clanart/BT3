# Q4089: VLMGP.forceUnLock - forceUnLock skips the settlement that unlock performs

## Question
VLMGP.sol: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Under a large vesting MGP distribution has just been queued into the vlMGP rewarder, is there an unprivileged sequence of `forceUnLock(uint256 _slotIndex)` that leaves `userInfos[user].factor in ReferralStorage` unreconciled with `getUserTotalLocked(user)`, violates the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large vesting MGP distribution has just been queued into the vlMGP rewarder, then assert `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)` end identical in both runs.
