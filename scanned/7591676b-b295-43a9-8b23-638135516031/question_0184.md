# Q0184: DSMath.wmul - fixed point operands are scaled by the caller

## Question
In libraries/DSMath.sol, the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Starting from a state where the attacker splits one lock across many addresses that each register a referral code, can an unprivileged EOA use `wmul(uint256 x, uint256 y)` to leave `userInfos[account].factor` inconsistent with `totalBoostFactor`, violating the invariant that the operand range of a fixed point helper must be bounded by protocol invariants and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wmul(uint256 x, uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: the attacker splits one lock across many addresses that each register a referral code.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `wmul(uint256 x, uint256 y)` sequence atomically under the attacker splits one lock across many addresses that each register a referral code, asserting at the end that `userInfos[account].factor` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
