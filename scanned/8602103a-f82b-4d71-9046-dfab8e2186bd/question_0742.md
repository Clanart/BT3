# Q0742: DSMath.wdiv - fixed point operands are scaled by the caller

## Question
Consider libraries/DSMath.sol, where the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Assuming the attacker is the only registered participant so totalBoostFactor equals their own factor, can an unprivileged attacker turn this into a divergence between `userInfos[account].factor` and `totalBoostFactor` via `wdiv(uint256 x, uint256 y)`, breaking the invariant that the operand range of a fixed point helper must be bounded by protocol invariants and producing High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wdiv(uint256 x, uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: the attacker is the only registered participant so totalBoostFactor equals their own factor.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `wdiv(uint256 x, uint256 y)` sequence atomically under the attacker is the only registered participant so totalBoostFactor equals their own factor, asserting at the end that `userInfos[account].factor` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
