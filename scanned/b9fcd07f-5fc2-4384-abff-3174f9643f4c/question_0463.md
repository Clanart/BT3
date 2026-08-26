# Q0463: DSMath.wdiv - the factor is recomputed from a live balance rather than accumulated

## Question
Consider libraries/DSMath.sol, where because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Assuming the attacker locks an amount whose square root truncates to zero, can an unprivileged attacker turn this into a divergence between `WAD` and `the operand scale used by the caller` via `wdiv(uint256 x, uint256 y)`, breaking the invariant that a share of a shared budget must be earned over time, not rewritten by the current balance and producing High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wdiv(uint256 x, uint256 y)` (mechanism: the factor is recomputed from a live balance rather than accumulated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wdiv(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Precondition: the attacker locks an amount whose square root truncates to zero.
- Invariant to test: a share of a shared budget must be earned over time, not rewritten by the current balance; concretely, `WAD` must stay reconciled with `the operand scale used by the caller`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker locks an amount whose square root truncates to zero, then assert `WAD` and `the operand scale used by the caller` end identical in both runs.
