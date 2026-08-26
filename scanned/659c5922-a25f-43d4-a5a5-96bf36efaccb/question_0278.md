# Q0278: LogExpMath.ln - truncation in the fixed point conversion favours one side

## Question
libraries/LogExpMath.sol: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. With the operand range reached through the Wombat pricing that SmartWomConvert reads under attacker control and the attacker has pushed the wom/mWom pool far off peg in the same transaction, can an unprivileged caller sequence `ln(int256 a)` so that `currentRatio() in SmartWomConvert` and `the value returned by the underlying math` no longer reconcile, violating the invariant that rounding on a value path must not consistently favour the party who chooses the amounts and realising High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: truncation in the fixed point conversion favours one side)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Precondition: the attacker has pushed the wom/mWom pool far off peg in the same transaction.
- Invariant to test: rounding on a value path must not consistently favour the party who chooses the amounts; concretely, `currentRatio() in SmartWomConvert` must stay reconciled with `the value returned by the underlying math`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the operand range reached through the Wombat pricing that SmartWomConvert reads) under the attacker has pushed the wom/mWom pool far off peg in the same transaction, asserting on every row that rounding on a value path must not consistently favour the party who chooses the amounts.
