# Q1149: VLMGP.forceUnLock - forceUnLock skips the settlement that unlock performs

## Question
In VLMGP.sol, unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Starting from a state where the attacker's slot matured exactly one second ago, can an unprivileged EOA use `forceUnLock(uint256 _slotIndex)` to leave `maxSlot` inconsistent with `userUnlockings[user].length`, violating the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker's slot matured exactly one second ago, then assert `maxSlot` and `userUnlockings[user].length` end identical in both runs.
