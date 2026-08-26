# Q0091: DSMath.sqrt - the factor is recomputed from a live balance rather than accumulated

## Question
libraries/DSMath.sol - because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under the attacker splits one lock across many addresses that each register a referral code, exploit this through `sqrt(uint256 y)` to break the reconciliation between `WAD` and `the operand scale used by the caller` and the invariant that a share of a shared budget must be earned over time, not rewritten by the current balance, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: the factor is recomputed from a live balance rather than accumulated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Precondition: the attacker splits one lock across many addresses that each register a referral code.
- Invariant to test: a share of a shared budget must be earned over time, not rewritten by the current balance; concretely, `WAD` must stay reconciled with `the operand scale used by the caller`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker splits one lock across many addresses that each register a referral code, call `sqrt(uint256 y)`, and assert `WAD` equals `the operand scale used by the caller` and that no account can withdraw more than it put in.
