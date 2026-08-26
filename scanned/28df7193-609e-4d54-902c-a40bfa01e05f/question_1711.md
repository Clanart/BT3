# Q1711: VLMGP.forceUnLock - forceUnLock skips the settlement that unlock performs

## Question
In VLMGP.sol, unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Does `forceUnLock(uint256 _slotIndex)` let an unprivileged caller exploit that under coolDownInSecs is at its configured production value and endTime is far in the future, so that `getUserTotalLocked(user)` diverges from `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`, the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under coolDownInSecs is at its configured production value and endTime is far in the future, asserting on every row that every exit path must settle vesting accrual under identical rules before reducing the lock.
