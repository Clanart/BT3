# Q3422: VLMGP.forceUnLock - forceUnLock skips the settlement that unlock performs

## Question
VLMGP.sol - unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Can an unprivileged attacker controlling _slotIndex and the exact point inside the cooldown curve at which the penalty is priced, under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, exploit this through `forceUnLock(uint256 _slotIndex)` to break the reconciliation between `userUnlockings[user][i].endTime` and `block.timestamp` and the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock, yielding High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, asserting on every row that every exit path must settle vesting accrual under identical rules before reducing the lock.
