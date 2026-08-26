# Q1144: DSMath.wmul - the factor is recomputed from a live balance rather than accumulated

## Question
Consider libraries/DSMath.sol, where because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Assuming BoostPoint is configured at a large fraction of DENOMINATOR, can an unprivileged attacker turn this into a divergence between `userInfos[account].factor` and `totalBoostFactor` via `wmul(uint256 x, uint256 y)`, breaking the invariant that a share of a shared budget must be earned over time, not rewritten by the current balance and producing High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wmul(uint256 x, uint256 y)` (mechanism: the factor is recomputed from a live balance rather than accumulated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Precondition: BoostPoint is configured at a large fraction of DENOMINATOR.
- Invariant to test: a share of a shared budget must be earned over time, not rewritten by the current balance; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange BoostPoint is configured at a large fraction of DENOMINATOR, call `wmul(uint256 x, uint256 y)`, and assert `userInfos[account].factor` equals `totalBoostFactor` and that no account can withdraw more than it put in.
