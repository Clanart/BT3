# Q0836: LogExpMath.ln - truncation in the fixed point conversion favours one side

## Question
libraries/LogExpMath.sol: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Under the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, is there an unprivileged sequence of `ln(int256 a)` that leaves `the exponent operand` unreconciled with `the bounds enforced before the call`, violates the invariant that rounding on a value path must not consistently favour the party who chooses the amounts, and delivers High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `ln(int256 a)` (mechanism: truncation in the fixed point conversion favours one side)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `ln(int256 a)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand range reached through the Wombat pricing that SmartWomConvert reads
- Exploit idea: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Precondition: the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee.
- Invariant to test: rounding on a value path must not consistently favour the party who chooses the amounts; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the operand range reached through the Wombat pricing that SmartWomConvert reads) under the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, asserting on every row that rounding on a value path must not consistently favour the party who chooses the amounts.
