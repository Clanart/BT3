# Q1022: LogExpMath.exp - truncation in the fixed point conversion favours one side

## Question
In libraries/LogExpMath.sol, the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Can an unprivileged attacker reach this through `exp(int256 x)` while the attacker routes many small conversions rather than one large one, and drive `the exponent operand` out of agreement with `the bounds enforced before the call` - breaking the invariant that rounding on a value path must not consistently favour the party who chooses the amounts - for High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `exp(int256 x)` (mechanism: truncation in the fixed point conversion favours one side)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `exp(int256 x)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction
- Exploit idea: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Precondition: the attacker routes many small conversions rather than one large one.
- Invariant to test: rounding on a value path must not consistently favour the party who chooses the amounts; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `exp(int256 x)` sequence atomically under the attacker routes many small conversions rather than one large one, asserting at the end that `the exponent operand` still equals `the bounds enforced before the call` and the PoC's balance delta is non-positive.
