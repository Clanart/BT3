# Q0123: LogExpMath.exp - the math is driven by pool state the caller can move

## Question
In libraries/LogExpMath.sol, SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Starting from a state where the attacker has pushed the wom/mWom pool far off peg in the same transaction, can an unprivileged EOA use `exp(int256 x)` to leave `currentRatio() in SmartWomConvert` inconsistent with `the value returned by the underlying math`, violating the invariant that the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `exp(int256 x)` (mechanism: the math is driven by pool state the caller can move)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `exp(int256 x)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction
- Exploit idea: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Precondition: the attacker has pushed the wom/mWom pool far off peg in the same transaction.
- Invariant to test: the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state; concretely, `currentRatio() in SmartWomConvert` must stay reconciled with `the value returned by the underlying math`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has pushed the wom/mWom pool far off peg in the same transaction, then assert `currentRatio() in SmartWomConvert` and `the value returned by the underlying math` end identical in both runs.
