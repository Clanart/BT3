# Q4285: VLMGP.unlock - forceUnLock skips the settlement that unlock performs

## Question
VLMGP.sol: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. With _slotIndex and how long after endTime the slot is redeemed under attacker control and the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, can an unprivileged caller sequence `unlock(uint256 _slotIndex)` so that `totalPenalty` and `IERC20(MGP).balanceOf(address(this))` no longer reconcile, violating the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the victim has a large unsettled userRewards balance in vlMGPBaseRewarder.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `unlock(uint256 _slotIndex)` sequence atomically under the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, asserting at the end that `totalPenalty` still equals `IERC20(MGP).balanceOf(address(this))` and the PoC's balance delta is non-positive.
