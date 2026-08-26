# Q1053: LogExpMath.ln - the math is driven by pool state the caller can move

## Question
Note that in libraries/LogExpMath.sol, SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Can an attacker holding only tokens bought on market reach it via `ln(int256 a)` under the attacker routes many small conversions rather than one large one and force `maxSwapAmount() in SmartWomConvert` apart from `IAsset cash and liability`, breaking the invariant that the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state for High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: the math is driven by pool state the caller can move)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Precondition: the attacker routes many small conversions rather than one large one.
- Invariant to test: the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state; concretely, `maxSwapAmount() in SmartWomConvert` must stay reconciled with `IAsset cash and liability`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker routes many small conversions rather than one large one, have the attacker run `ln(int256 a)`, then assert the victim's claimable value and the `maxSwapAmount() in SmartWomConvert` versus `IAsset cash and liability` relation are unchanged by the attacker's transaction.
