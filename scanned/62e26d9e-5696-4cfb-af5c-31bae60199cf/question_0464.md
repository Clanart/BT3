# Q0464: LogExpMath.exp - truncation in the fixed point conversion favours one side

## Question
Consider libraries/LogExpMath.sol, where the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Assuming womCash exceeds womLiability so the swap ceiling collapses to zero, can an unprivileged attacker turn this into a divergence between `currentRatio() in SmartWomConvert` and `the value returned by the underlying math` via `exp(int256 x)`, breaking the invariant that rounding on a value path must not consistently favour the party who chooses the amounts and producing High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `exp(int256 x)` (mechanism: truncation in the fixed point conversion favours one side)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `exp(int256 x)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction
- Exploit idea: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Precondition: womCash exceeds womLiability so the swap ceiling collapses to zero.
- Invariant to test: rounding on a value path must not consistently favour the party who chooses the amounts; concretely, `currentRatio() in SmartWomConvert` must stay reconciled with `the value returned by the underlying math`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `exp(int256 x)` sequence atomically under womCash exceeds womLiability so the swap ceiling collapses to zero, asserting at the end that `currentRatio() in SmartWomConvert` still equals `the value returned by the underlying math` and the PoC's balance delta is non-positive.
