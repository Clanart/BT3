# Q0588: LogExpMath.pow - the math is driven by pool state the caller can move

## Question
In libraries/LogExpMath.sol, SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Starting from a state where the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, can an unprivileged EOA use `pow(uint256 x, uint256 y)` to leave `maxSwapAmount() in SmartWomConvert` inconsistent with `IAsset cash and liability`, violating the invariant that the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/LogExpMath.sol -> `pow(uint256 x, uint256 y)` (mechanism: the math is driven by pool state the caller can move)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `pow(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the Wombat pool state consumed by SmartWomConvert.currentRatio, which the attacker moves before calling smartConvert or convert
- Exploit idea: SmartWomConvert.currentRatio and maxSwapAmount feed values derived from live Wombat asset state into this math, so the operand range is set by whoever moved the pool immediately before the call. Precondition: the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee.
- Invariant to test: the operand range of a pricing routine must be bounded by protocol invariants, not by an attacker-set pool state; concretely, `maxSwapAmount() in SmartWomConvert` must stay reconciled with `IAsset cash and liability`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the harvest path is reached through WombatStaking._sendRewards with an mWOM-flagged fee, call `pow(uint256 x, uint256 y)`, and assert `maxSwapAmount() in SmartWomConvert` equals `IAsset cash and liability` and that no account can withdraw more than it put in.
