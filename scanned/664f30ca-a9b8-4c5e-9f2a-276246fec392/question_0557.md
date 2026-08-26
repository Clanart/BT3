# Q0557: LogExpMath.ln - truncation in the fixed point conversion favours one side

## Question
In libraries/LogExpMath.sol, the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Can an unprivileged attacker reach this through `ln(int256 a)` while womCash exceeds womLiability so the swap ceiling collapses to zero, and drive `maxSwapAmount() in SmartWomConvert` out of agreement with `IAsset cash and liability` - breaking the invariant that rounding on a value path must not consistently favour the party who chooses the amounts - for High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: truncation in the fixed point conversion favours one side)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Precondition: womCash exceeds womLiability so the swap ceiling collapses to zero.
- Invariant to test: rounding on a value path must not consistently favour the party who chooses the amounts; concretely, `maxSwapAmount() in SmartWomConvert` must stay reconciled with `IAsset cash and liability`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the operand range reached through the Wombat pricing that SmartWomConvert reads) under womCash exceeds womLiability so the swap ceiling collapses to zero, asserting on every row that rounding on a value path must not consistently favour the party who chooses the amounts.
