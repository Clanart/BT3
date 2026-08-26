# Q0711: DSMath.wdiv - the factor is recomputed from a live balance rather than accumulated

## Question
Consider libraries/DSMath.sol, where because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Assuming the attacker is the only registered participant so totalBoostFactor equals their own factor, can an unprivileged attacker turn this into a divergence between `DSMath.sqrt(lockedAmount)` and `userInfos[account].factor in ReferralStorage` via `wdiv(uint256 x, uint256 y)`, breaking the invariant that a share of a shared budget must be earned over time, not rewritten by the current balance and producing High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wdiv(uint256 x, uint256 y)` (mechanism: the factor is recomputed from a live balance rather than accumulated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Precondition: the attacker is the only registered participant so totalBoostFactor equals their own factor.
- Invariant to test: a share of a shared budget must be earned over time, not rewritten by the current balance; concretely, `DSMath.sqrt(lockedAmount)` must stay reconciled with `userInfos[account].factor in ReferralStorage`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker is the only registered participant so totalBoostFactor equals their own factor, snapshot `DSMath.sqrt(lockedAmount)` and `userInfos[account].factor in ReferralStorage`, run the attacker's `wdiv(uint256 x, uint256 y)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
