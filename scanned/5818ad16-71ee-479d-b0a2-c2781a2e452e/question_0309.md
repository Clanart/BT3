# Q0309: LogExpMath.pow - the math is driven by pool state the caller can move

## Question
libraries/LogExpMath.sol - SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Can an unprivileged attacker controlling the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert, under womCash exceeds womLiability so the swap ceiling collapses to zero, exploit this through `pow(uint256 x, uint256 y)` to break the reconciliation between `currentRatio() in SmartWomConvert` and `the value returned by the underlying math` and the invariant that the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `pow(uint256 x, uint256 y)` (mechanism: the math is driven by pool state the caller can move)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `pow(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert
- Exploit idea: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Precondition: womCash exceeds womLiability so the swap ceiling collapses to zero.
- Invariant to test: the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state; concretely, `currentRatio() in SmartWomConvert` must stay reconciled with `the value returned by the underlying math`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange womCash exceeds womLiability so the swap ceiling collapses to zero, call `pow(uint256 x, uint256 y)`, and assert `currentRatio() in SmartWomConvert` equals `the value returned by the underlying math` and that no account can withdraw more than it put in.
