# Q0618: DSMath.sqrt - fixed point operands are scaled by the caller

## Question
libraries/DSMath.sol - the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under the attacker is the only registered participant so totalBoostFactor equals their own factor, exploit this through `sqrt(uint256 y)` to break the reconciliation between `WAD` and `the operand scale used by the caller` and the invariant that the operand range of a fixed point helper must be bounded by protocol invariants, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: the attacker is the only registered participant so totalBoostFactor equals their own factor.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `WAD` must stay reconciled with `the operand scale used by the caller`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking) under the attacker is the only registered participant so totalBoostFactor equals their own factor, asserting on every row that the operand range of a fixed point helper must be bounded by protocol invariants.
