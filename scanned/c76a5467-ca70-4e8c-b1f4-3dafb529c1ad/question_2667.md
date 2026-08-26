# Q2667: VLMGP.forceUnLock - forceUnLock skips the settlement that unlock performs

## Question
VLMGP.sol: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. With _slotIndex and the exact point inside the cooldown curve at which the penalty is priced under attacker control and the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, can an unprivileged caller sequence `forceUnLock(uint256 _slotIndex)` so that `totalAmount` and `sum of userInfo[vlmgp][*].amount in MasterMagpie` no longer reconcile, violating the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, have the attacker run `forceUnLock(uint256 _slotIndex)`, then assert the victim's claimable value and the `totalAmount` versus `sum of userInfo[vlmgp][*].amount in MasterMagpie` relation are unchanged by the attacker's transaction.
