# Q0494: DSMath.wdiv - fixed point operands are scaled by the caller

## Question
Consider libraries/DSMath.sol, where the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Assuming the attacker locks an amount whose square root truncates to zero, can an unprivileged attacker turn this into a divergence between `DSMath.sqrt(lockedAmount)` and `userInfos[account].factor in ReferralStorage` via `wdiv(uint256 x, uint256 y)`, breaking the invariant that the operand range of a fixed point helper must be bounded by protocol invariants and producing High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wdiv(uint256 x, uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: the attacker locks an amount whose square root truncates to zero.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `DSMath.sqrt(lockedAmount)` must stay reconciled with `userInfos[account].factor in ReferralStorage`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker locks an amount whose square root truncates to zero, snapshot `DSMath.sqrt(lockedAmount)` and `userInfos[account].factor in ReferralStorage`, run the attacker's `wdiv(uint256 x, uint256 y)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
