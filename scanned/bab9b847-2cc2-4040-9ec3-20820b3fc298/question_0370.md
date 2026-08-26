# Q0370: DSMath.sqrt - fixed point operands are scaled by the caller

## Question
libraries/DSMath.sol - the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under the attacker locks an amount whose square root truncates to zero, exploit this through `sqrt(uint256 y)` to break the reconciliation between `userInfos[account].factor` and `totalBoostFactor` and the invariant that the operand range of a fixed point helper must be bounded by protocol invariants, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: the attacker locks an amount whose square root truncates to zero.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `sqrt(uint256 y)`: constrain the setup so that the attacker locks an amount whose square root truncates to zero, fuzz the attacker inputs (the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking), and assert after every call that the operand range of a fixed point helper must be bounded by protocol invariants.
