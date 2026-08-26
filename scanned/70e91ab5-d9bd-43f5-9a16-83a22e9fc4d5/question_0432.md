# Q0432: DSMath.wmul - fixed point operands are scaled by the caller

## Question
In libraries/DSMath.sol, the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Starting from a state where the attacker locks an amount whose square root truncates to zero, can an unprivileged EOA use `wmul(uint256 x, uint256 y)` to leave `WAD` inconsistent with `the operand scale used by the caller`, violating the invariant that the operand range of a fixed point helper must be bounded by protocol invariants and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wmul(uint256 x, uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: the attacker locks an amount whose square root truncates to zero.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `WAD` must stay reconciled with `the operand scale used by the caller`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker locks an amount whose square root truncates to zero, call `wmul(uint256 x, uint256 y)`, and assert `WAD` equals `the operand scale used by the caller` and that no account can withdraw more than it put in.
