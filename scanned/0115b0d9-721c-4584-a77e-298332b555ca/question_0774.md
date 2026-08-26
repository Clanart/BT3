# Q0774: LogExpMath.ln - the math is driven by pool state the caller can move

## Question
libraries/LogExpMath.sol: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Under the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, is there an unprivileged sequence of `ln(int256 a)` that leaves `currentRatio() in SmartWomConvert` unreconciled with `the value returned by the underlying math`, violates the invariant that the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state, and delivers High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: the math is driven by pool state the caller can move)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Precondition: the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee.
- Invariant to test: the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state; concretely, `currentRatio() in SmartWomConvert` must stay reconciled with `the value returned by the underlying math`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, have the attacker run `ln(int256 a)`, then assert the victim's claimable value and the `currentRatio() in SmartWomConvert` versus `the value returned by the underlying math` relation are unchanged by the attacker's transaction.
