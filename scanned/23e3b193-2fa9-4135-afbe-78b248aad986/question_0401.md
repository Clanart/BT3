# Q0401: DSMath.wmul - the factor is recomputed from a live balance rather than accumulated

## Question
In libraries/DSMath.sol, because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Starting from a state where the attacker locks an amount whose square root truncates to zero, can an unprivileged EOA use `wmul(uint256 x, uint256 y)` to leave `userInfos[account].factor` inconsistent with `totalBoostFactor`, violating the invariant that a share of a shared budget must be earned over time, not rewritten by the current balance and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wmul(uint256 x, uint256 y)` (mechanism: the factor is recomputed from a live balance rather than accumulated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Precondition: the attacker locks an amount whose square root truncates to zero.
- Invariant to test: a share of a shared budget must be earned over time, not rewritten by the current balance; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `wmul(uint256 x, uint256 y)` sequence atomically under the attacker locks an amount whose square root truncates to zero, asserting at the end that `userInfos[account].factor` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
