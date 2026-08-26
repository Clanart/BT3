# Q2938: VLMGP.unlock - forceUnLock skips the settlement that unlock performs

## Question
Note that in VLMGP.sol, unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Can an attacker holding only tokens bought on market reach it via `unlock(uint256 _slotIndex)` under the pool the attacker voted for has since been deactivated so unvote reverts and force `getUserAmountInCoolDown(user)` apart from `totalAmountInCoolDown`, breaking the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `unlock(uint256 _slotIndex)` sequence atomically under the pool the attacker voted for has since been deactivated so unvote reverts, asserting at the end that `getUserAmountInCoolDown(user)` still equals `totalAmountInCoolDown` and the PoC's balance delta is non-positive.
