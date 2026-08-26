# Q0402: LogExpMath.exp - the math is driven by pool state the caller can move

## Question
Consider libraries/LogExpMath.sol, where SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Assuming womCash exceeds womLiability so the swap ceiling collapses to zero, can an unprivileged attacker turn this into a divergence between `maxSwapAmount() in SmartWomConvert` and `IAsset cash and liability` via `exp(int256 x)`, breaking the invariant that the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state and producing High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `exp(int256 x)` (mechanism: the math is driven by pool state the caller can move)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `exp(int256 x)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction
- Exploit idea: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Precondition: womCash exceeds womLiability so the swap ceiling collapses to zero.
- Invariant to test: the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state; concretely, `maxSwapAmount() in SmartWomConvert` must stay reconciled with `IAsset cash and liability`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under womCash exceeds womLiability so the swap ceiling collapses to zero, then assert `maxSwapAmount() in SmartWomConvert` and `IAsset cash and liability` end identical in both runs.
