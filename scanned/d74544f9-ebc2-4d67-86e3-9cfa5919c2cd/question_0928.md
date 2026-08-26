# Q0928: DSMath.wmul - fixed point operands are scaled by the caller

## Question
In libraries/DSMath.sol, the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Starting from a state where the attacker raises and lowers their lock repeatedly across blocks, can an unprivileged EOA use `wmul(uint256 x, uint256 y)` to leave `userInfos[account].factor` inconsistent with `totalBoostFactor`, violating the invariant that the operand range of a fixed point helper must be bounded by protocol invariants and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wmul(uint256 x, uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: the attacker raises and lowers their lock repeatedly across blocks.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `wmul(uint256 x, uint256 y)`: constrain the setup so that the attacker raises and lowers their lock repeatedly across blocks, fuzz the attacker inputs (the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims), and assert after every call that the operand range of a fixed point helper must be bounded by protocol invariants.
