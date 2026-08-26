# Q0185: LogExpMath.exp - truncation in the fixed point conversion favours one side

## Question
In libraries/LogExpMath.sol, the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Starting from a state where the attacker has pushed the wom/mWom pool far off peg in the same transaction, can an unprivileged EOA use `exp(int256 x)` to leave `the exponent operand` inconsistent with `the bounds enforced before the call`, violating the invariant that rounding on a value path must not consistently favour the party who chooses the amounts and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `exp(int256 x)` (mechanism: truncation in the fixed point conversion favours one side)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `exp(int256 x)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction
- Exploit idea: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Precondition: the attacker has pushed the wom/mWom pool far off peg in the same transaction.
- Invariant to test: rounding on a value path must not consistently favour the party who chooses the amounts; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `exp(int256 x)` sequence atomically under the attacker has pushed the wom/mWom pool far off peg in the same transaction, asserting at the end that `the exponent operand` still equals `the bounds enforced before the call` and the PoC's balance delta is non-positive.
