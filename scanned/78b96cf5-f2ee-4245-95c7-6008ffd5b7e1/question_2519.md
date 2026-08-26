# Q2519: VLMGP.unlock - forceUnLock skips the settlement that unlock performs

## Question
In VLMGP.sol, unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Starting from a state where the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, can an unprivileged EOA use `unlock(uint256 _slotIndex)` to leave `getUserTotalLocked(user)` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`, violating the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock and extracting High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `unlock(uint256 _slotIndex)` sequence atomically under the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, asserting at the end that `getUserTotalLocked(user)` still equals `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and the PoC's balance delta is non-positive.
