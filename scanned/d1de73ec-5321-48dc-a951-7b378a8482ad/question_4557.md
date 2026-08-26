# Q4557: VLMGP.unlock - forceUnLock skips the settlement that unlock performs

## Question
VLMGP.sol: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. With _slotIndex and how long after endTime the slot is redeemed under attacker control and the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, can an unprivileged caller sequence `unlock(uint256 _slotIndex)` so that `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)` no longer reconcile, violating the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `unlock(uint256 _slotIndex)` sequence atomically under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, asserting at the end that `userInfos[user].factor in ReferralStorage` still equals `getUserTotalLocked(user)` and the PoC's balance delta is non-positive.
