# Q1052: DSMath.sqrt - sqrt truncation zeroes small participants

## Question
libraries/DSMath.sol - the integer square root truncates, so a lock small enough produces a factor of zero and contributes nothing to totalBoostFactor while still being treated as a registered participant. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under BoostPoint is configured at a large fraction of DENOMINATOR, exploit this through `sqrt(uint256 y)` to break the reconciliation between `WAD` and `the operand scale used by the caller` and the invariant that a weight function must not silently reduce a real participant to zero influence, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: sqrt truncation zeroes small participants)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: the integer square root truncates, so a lock small enough produces a factor of zero and contributes nothing to totalBoostFactor while still being treated as a registered participant. Precondition: BoostPoint is configured at a large fraction of DENOMINATOR.
- Invariant to test: a weight function must not silently reduce a real participant to zero influence; concretely, `WAD` must stay reconciled with `the operand scale used by the caller`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking) under BoostPoint is configured at a large fraction of DENOMINATOR, asserting on every row that a weight function must not silently reduce a real participant to zero influence.
