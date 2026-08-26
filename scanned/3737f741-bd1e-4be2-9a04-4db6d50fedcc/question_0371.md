# Q0371: LogExpMath.pow - truncation in the fixed point conversion favours one side

## Question
libraries/LogExpMath.sol - the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Can an unprivileged attacker controlling the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert, under womCash exceeds womLiability so the swap ceiling collapses to zero, exploit this through `pow(uint256 x, uint256 y)` to break the reconciliation between `the exponent operand` and `the bounds enforced before the call` and the invariant that rounding on a value path must not consistently favour the party who chooses the amounts, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `pow(uint256 x, uint256 y)` (mechanism: truncation in the fixed point conversion favours one side)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `pow(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert
- Exploit idea: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Precondition: womCash exceeds womLiability so the swap ceiling collapses to zero.
- Invariant to test: rounding on a value path must not consistently favour the party who chooses the amounts; concretely, `the exponent operand` must stay reconciled with `the bounds enforced before the call`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `pow(uint256 x, uint256 y)`: constrain the setup so that womCash exceeds womLiability so the swap ceiling collapses to zero, fuzz the attacker inputs (the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert), and assert after every call that rounding on a value path must not consistently favour the party who chooses the amounts.
