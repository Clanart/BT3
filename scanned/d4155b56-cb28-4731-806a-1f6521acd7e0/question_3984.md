# Q3984: VLMGP.unlock - forceUnLock skips the settlement that unlock performs

## Question
VLMGP.sol - unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Can an unprivileged attacker controlling _slotIndex and how long after endTime the slot is redeemed, under a large vesting MGP distribution has just been queued into the vlMGP rewarder, exploit this through `unlock(uint256 _slotIndex)` to break the reconciliation between `userUnlockings[user][i].endTime` and `block.timestamp` and the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock, yielding High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large vesting MGP distribution has just been queued into the vlMGP rewarder, then assert `userUnlockings[user][i].endTime` and `block.timestamp` end identical in both runs.
