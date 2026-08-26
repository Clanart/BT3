# Q4376: VLMGP.forceUnLock - forceUnLock skips the settlement that unlock performs

## Question
Note that in VLMGP.sol, unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Can an attacker holding only tokens bought on market reach it via `forceUnLock(uint256 _slotIndex)` under the victim has a large unsettled userRewards balance in vlMGPBaseRewarder and force `userTotalVotedInVlmgp(user) in WombatBribeManager` apart from `getUserTotalLocked(user)`, breaking the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the victim has a large unsettled userRewards balance in vlMGPBaseRewarder.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `forceUnLock(uint256 _slotIndex)` sequence atomically under the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, asserting at the end that `userTotalVotedInVlmgp(user) in WombatBribeManager` still equals `getUserTotalLocked(user)` and the PoC's balance delta is non-positive.
