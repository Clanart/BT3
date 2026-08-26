# Q0216: LogExpMath.ln - the math is driven by pool state the caller can move

## Question
libraries/LogExpMath.sol: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. With the operand range reached through the Wombat pricing that SmartWomConvert reads under attacker control and the attacker has pushed the wom/mWom pool far off peg in the same transaction, can an unprivileged caller sequence `ln(int256 a)` so that `maxSwapAmount() in SmartWomConvert` and `IAsset cash and liability` no longer reconcile, violating the invariant that the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state and realising High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: the math is driven by pool state the caller can move)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Precondition: the attacker has pushed the wom/mWom pool far off peg in the same transaction.
- Invariant to test: the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state; concretely, `maxSwapAmount() in SmartWomConvert` must stay reconciled with `IAsset cash and liability`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has pushed the wom/mWom pool far off peg in the same transaction, have the attacker run `ln(int256 a)`, then assert the victim's claimable value and the `maxSwapAmount() in SmartWomConvert` versus `IAsset cash and liability` relation are unchanged by the attacker's transaction.
