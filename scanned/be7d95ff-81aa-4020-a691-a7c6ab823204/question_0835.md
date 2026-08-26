# Q0835: DSMath.sqrt - the factor is recomputed from a live balance rather than accumulated

## Question
libraries/DSMath.sol - because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under the attacker raises and lowers their lock repeatedly across blocks, exploit this through `sqrt(uint256 y)` to break the reconciliation between `WAD` and `the operand scale used by the caller` and the invariant that a share of a shared budget must be earned over time, not rewritten by the current balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: the factor is recomputed from a live balance rather than accumulated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Precondition: the attacker raises and lowers their lock repeatedly across blocks.
- Invariant to test: a share of a shared budget must be earned over time, not rewritten by the current balance; concretely, `WAD` must stay reconciled with `the operand scale used by the caller`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking) under the attacker raises and lowers their lock repeatedly across blocks, asserting on every row that a share of a shared budget must be earned over time, not rewritten by the current balance.
