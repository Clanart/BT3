# Q3065: VLMGP.forceUnLock - forceUnLock skips the settlement that unlock performs

## Question
VLMGP.sol: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Under the pool the attacker voted for has since been deactivated so unvote reverts, is there an unprivileged sequence of `forceUnLock(uint256 _slotIndex)` that leaves `getRewardablePercentWAD(user)` unreconciled with `userUnlockings[user][i].amountInCoolDown`, violates the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool the attacker voted for has since been deactivated so unvote reverts, have the attacker run `forceUnLock(uint256 _slotIndex)`, then assert the victim's claimable value and the `getRewardablePercentWAD(user)` versus `userUnlockings[user][i].amountInCoolDown` relation are unchanged by the attacker's transaction.
