# Q1174: DSMath.wmul - fixed point operands are scaled by the caller

## Question
libraries/DSMath.sol: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. With the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims under attacker control and BoostPoint is configured at a large fraction of DENOMINATOR, can an unprivileged caller sequence `wmul(uint256 x, uint256 y)` so that `WAD` and `the operand scale used by the caller` no longer reconcile, violating the invariant that the operand range of a fixed point helper must be bounded by protocol invariants and realising High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wmul(uint256 x, uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: BoostPoint is configured at a large fraction of DENOMINATOR.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `WAD` must stay reconciled with `the operand scale used by the caller`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `wmul(uint256 x, uint256 y)` sequence atomically under BoostPoint is configured at a large fraction of DENOMINATOR, asserting at the end that `WAD` still equals `the operand scale used by the caller` and the PoC's balance delta is non-positive.
