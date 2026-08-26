# Q0680: DSMath.wmul - fixed point operands are scaled by the caller

## Question
In libraries/DSMath.sol, the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Starting from a state where the attacker is the only registered participant so totalBoostFactor equals their own factor, can an unprivileged EOA use `wmul(uint256 x, uint256 y)` to leave `DSMath.sqrt(lockedAmount)` inconsistent with `userInfos[account].factor in ReferralStorage`, violating the invariant that the operand range of a fixed point helper must be bounded by protocol invariants and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wmul(uint256 x, uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: the attacker is the only registered participant so totalBoostFactor equals their own factor.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `DSMath.sqrt(lockedAmount)` must stay reconciled with `userInfos[account].factor in ReferralStorage`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker is the only registered participant so totalBoostFactor equals their own factor, have the attacker run `wmul(uint256 x, uint256 y)`, then assert the victim's claimable value and the `DSMath.sqrt(lockedAmount)` versus `userInfos[account].factor in ReferralStorage` relation are unchanged by the attacker's transaction.
