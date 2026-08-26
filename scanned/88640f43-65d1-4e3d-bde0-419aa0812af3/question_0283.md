# Q0283: VLMGP.unlock - forceUnLock skips the settlement that unlock performs

## Question
VLMGP.sol: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, is there an unprivileged sequence of `unlock(uint256 _slotIndex)` that leaves `totalPenalty` unreconciled with `IERC20(MGP).balanceOf(address(this))`, violates the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock, and delivers High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `unlock(uint256 _slotIndex)` sequence atomically under the attacker has just called startUnlock so every slot is inside its cooldown window and the rewardable percent is still 1e18, asserting at the end that `totalPenalty` still equals `IERC20(MGP).balanceOf(address(this))` and the PoC's balance delta is non-positive.
