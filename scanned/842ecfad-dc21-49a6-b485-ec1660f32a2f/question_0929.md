# Q0929: LogExpMath.pow - truncation in the fixed point conversion favours one side

## Question
Consider libraries/LogExpMath.sol, where the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Assuming the attacker routes many small conversions rather than one large one, can an unprivileged attacker turn this into a divergence between `maxSwapAmount() in SmartWomConvert` and `IAsset cash and liability` via `pow(uint256 x, uint256 y)`, breaking the invariant that rounding on a value path must not consistently favour the party who chooses the amounts and producing High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `pow(uint256 x, uint256 y)` (mechanism: truncation in the fixed point conversion favours one side)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `pow(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert
- Exploit idea: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Precondition: the attacker routes many small conversions rather than one large one.
- Invariant to test: rounding on a value path must not consistently favour the party who chooses the amounts; concretely, `maxSwapAmount() in SmartWomConvert` must stay reconciled with `IAsset cash and liability`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `pow(uint256 x, uint256 y)`: constrain the setup so that the attacker routes many small conversions rather than one large one, fuzz the attacker inputs (the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert), and assert after every call that rounding on a value path must not consistently favour the party who chooses the amounts.
