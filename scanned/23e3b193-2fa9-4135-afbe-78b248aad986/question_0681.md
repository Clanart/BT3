# Q0681: LogExpMath.exp - the math is driven by pool state the caller can move

## Question
libraries/LogExpMath.sol: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. With the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction under attacker control and the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, can an unprivileged caller sequence `exp(int256 x)` so that `the exponent operand` and `the bounds enforced before the call` no longer reconcile, violating the invariant that the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state and realising High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `exp(int256 x)` (mechanism: the math is driven by pool state the caller can move)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `exp(int256 x)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction
- Exploit idea: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Precondition: the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee.
- Invariant to test: the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, then assert `the exponent operand` and `the bounds enforced before the call` end identical in both runs.
