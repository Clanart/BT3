# Q4641: VLMGP.forceUnLock - forceUnLock skips the settlement that unlock performs

## Question
VLMGP.sol: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, is there an unprivileged sequence of `forceUnLock(uint256 _slotIndex)` that leaves `maxSlot` unreconciled with `userUnlockings[user].length`, violates the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `forceUnLock(uint256 _slotIndex)` sequence atomically under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, asserting at the end that `maxSlot` still equals `userUnlockings[user].length` and the PoC's balance delta is non-positive.
