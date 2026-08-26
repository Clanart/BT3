# Q1114: DSMath.sqrt - fixed point operands are scaled by the caller

## Question
libraries/DSMath.sol - the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under BoostPoint is configured at a large fraction of DENOMINATOR, exploit this through `sqrt(uint256 y)` to break the reconciliation between `userInfos[account].factor` and `totalBoostFactor` and the invariant that the operand range of a fixed point helper must be bounded by protocol invariants, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: BoostPoint is configured at a large fraction of DENOMINATOR.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up BoostPoint is configured at a large fraction of DENOMINATOR, snapshot `userInfos[account].factor` and `totalBoostFactor`, run the attacker's `sqrt(uint256 y)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
