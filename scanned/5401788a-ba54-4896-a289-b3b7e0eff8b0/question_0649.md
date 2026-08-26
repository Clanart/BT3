# Q0649: DSMath.wmul - the factor is recomputed from a live balance rather than accumulated

## Question
In libraries/DSMath.sol, because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Starting from a state where the attacker is the only registered participant so totalBoostFactor equals their own factor, can an unprivileged EOA use `wmul(uint256 x, uint256 y)` to leave `WAD` inconsistent with `the operand scale used by the caller`, violating the invariant that a share of a shared budget must be earned over time, not rewritten by the current balance and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wmul(uint256 x, uint256 y)` (mechanism: the factor is recomputed from a live balance rather than accumulated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Precondition: the attacker is the only registered participant so totalBoostFactor equals their own factor.
- Invariant to test: a share of a shared budget must be earned over time, not rewritten by the current balance; concretely, `WAD` must stay reconciled with `the operand scale used by the caller`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is the only registered participant so totalBoostFactor equals their own factor, call `wmul(uint256 x, uint256 y)`, and assert `WAD` equals `the operand scale used by the caller` and that no account can withdraw more than it put in.
