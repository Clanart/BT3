# Q1533: VLMGP.unlock - forceUnLock skips the settlement that unlock performs

## Question
VLMGP.sol: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. With _slotIndex and how long after endTime the slot is redeemed under attacker control and coolDownInSecs is at its configured production value and endTime is far in the future, can an unprivileged caller sequence `unlock(uint256 _slotIndex)` so that `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` no longer reconcile, violating the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under coolDownInSecs is at its configured production value and endTime is far in the future, then assert `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` end identical in both runs.
