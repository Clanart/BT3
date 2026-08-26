# Q0934: VLMGP.unlock - forceUnLock skips the settlement that unlock performs

## Question
Consider VLMGP.sol, where unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Assuming the attacker's slot matured exactly one second ago, can an unprivileged attacker turn this into a divergence between `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)` via `unlock(uint256 _slotIndex)`, breaking the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock and producing High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker's slot matured exactly one second ago, have the attacker run `unlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `userInfos[user].factor in ReferralStorage` versus `getUserTotalLocked(user)` relation are unchanged by the attacker's transaction.
