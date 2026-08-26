# Q0030: LogExpMath.pow - the math is driven by pool state the caller can move

## Question
In libraries/LogExpMath.sol, SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Does `pow(uint256 x, uint256 y)` let an unprivileged caller exploit that under the attacker has pushed the wom/mWom pool far off peg in the same transaction, so that `the exponent operand` diverges from `the bounds enforced before the call`, the invariant that the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `pow(uint256 x, uint256 y)` (mechanism: the math is driven by pool state the caller can move)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `pow(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert
- Exploit idea: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Precondition: the attacker has pushed the wom/mWom pool far off peg in the same transaction.
- Invariant to test: the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has pushed the wom/mWom pool far off peg in the same transaction, call `pow(uint256 x, uint256 y)`, and assert `the exponent operand` equals `the bounds enforced before the call` and that no account can withdraw more than it put in.
