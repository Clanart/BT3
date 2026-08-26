# Q3764: VLMGP.forceUnLock - forceUnLock skips the settlement that unlock performs

## Question
Consider VLMGP.sol, where unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Assuming the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, can an unprivileged attacker turn this into a divergence between `totalPenalty` and `IERC20(MGP).balanceOf(address(this))` via `forceUnLock(uint256 _slotIndex)`, breaking the invariant that every exit path must settle vesting accrual under identical rules before reducing the lock and producing High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `forceUnLock(uint256 _slotIndex)` (mechanism: forceUnLock skips the settlement that unlock performs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `forceUnLock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the exact point inside the cooldown curve at which the penalty is priced
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor before touching the slot, but forceUnLock() goes straight to _unlock() and expectedPenaltyAmount() with no settlement, so the position leaves the lock with its vesting accrual still priced at the pre-exit state. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: every exit path must settle vesting accrual under identical rules before reducing the lock; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the exact point inside the cooldown curve at which the penalty is priced) under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, asserting on every row that every exit path must settle vesting accrual under identical rules before reducing the lock.
