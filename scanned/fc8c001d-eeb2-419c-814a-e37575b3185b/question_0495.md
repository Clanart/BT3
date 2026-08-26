# Q0495: LogExpMath.ln - the math is driven by pool state the caller can move

## Question
In libraries/LogExpMath.sol, SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Can an unprivileged attacker reach this through `ln(int256 a)` while womCash exceeds womLiability so the swap ceiling collapses to zero, and drive `the exponent operand` out of agreement with `the bounds enforced before the call` - breaking the invariant that the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state - for High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: the math is driven by pool state the caller can move)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Precondition: womCash exceeds womLiability so the swap ceiling collapses to zero.
- Invariant to test: the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish womCash exceeds womLiability so the swap ceiling collapses to zero, have the attacker run `ln(int256 a)`, then assert the victim's claimable value and the `the exponent operand` versus `the bounds enforced before the call` relation are unchanged by the attacker's transaction.
