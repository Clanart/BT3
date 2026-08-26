# Q4809: VLMGP.unlock - forceUnLock skips the settlement that unlock performs

## Question
In VLMGP.sol, unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Does `unlock(uint256 _slotIndex)` let an unprivileged caller exploit that under the attacker repeats cancelUnlock and startUnlock inside a single transaction, so that `userTotalVotedInVlmgp(user) in WombatBribeManager` diverges from `getUserTotalLocked(user)`, the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker repeats cancelUnlock and startUnlock inside a single transaction.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker repeats cancelUnlock and startUnlock inside a single transaction, have the attacker run `unlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `userTotalVotedInVlmgp(user) in WombatBribeManager` versus `getUserTotalLocked(user)` relation are unchanged by the attacker's transaction.
