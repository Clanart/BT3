# Q0650: LogExpMath.pow - truncation in the fixed point conversion favours one side

## Question
In libraries/LogExpMath.sol, the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Starting from a state where the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, can an unprivileged EOA use `pow(uint256 x, uint256 y)` to leave `currentRatio() in SmartWomConvert` inconsistent with `the value returned by the underlying math`, violating the invariant that rounding on a value path must not consistently favour the party who chooses the amounts and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `pow(uint256 x, uint256 y)` (mechanism: truncation in the fixed point conversion favours one side)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `pow(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert
- Exploit idea: the fixed point conversions truncate rather than round, so the direction of the loss is fixed and an attacker who repeatedly routes small amounts through the path accumulates the residue. Precondition: the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee.
- Invariant to test: rounding on a value path must not consistently favour the party who chooses the amounts; concretely, `currentRatio() in SmartWomConvert` must stay reconciled with `the value returned by the underlying math`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `pow(uint256 x, uint256 y)`: constrain the setup so that the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, fuzz the attacker inputs (the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert), and assert after every call that rounding on a value path must not consistently favour the party who chooses the amounts.
