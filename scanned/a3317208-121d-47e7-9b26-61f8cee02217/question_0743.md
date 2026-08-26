# Q0743: LogExpMath.exp - truncation in the fixed point conversion favours one side

## Question
libraries/LogExpMath.sol: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. With the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction under attacker control and the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, can an unprivileged caller sequence `exp(int256 x)` so that `maxSwapAmount() in SmartWomConvert` and `IAsset cash and liability` no longer reconcile, violating the invariant that rounding on a value path must not consistently favour the party who chooses the amounts and realising High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `exp(int256 x)` (mechanism: truncation in the fixed point conversion favours one side)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `exp(int256 x)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.maxSwapAmount, which the attacker moves in the same transaction
- Exploit idea: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Precondition: the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee.
- Invariant to test: rounding on a value path must not consistently favour the party who chooses the amounts; concretely, `maxSwapAmount() in SmartWomConvert` must stay reconciled with `IAsset cash and liability`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `exp(int256 x)` sequence atomically under the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, asserting at the end that `maxSwapAmount() in SmartWomConvert` still equals `IAsset cash and liability` and the PoC's balance delta is non-positive.
