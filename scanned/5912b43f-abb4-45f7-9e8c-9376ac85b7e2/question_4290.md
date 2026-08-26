# Q4290: SmartWomConvert.smartConvert - smartConvert is on the protocol harvest path

## Question
In wombat/SmartWomConvert.sol, WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Does `smartConvert(uint256 _amountIn, uint256 _mode)` let an unprivileged caller exploit that under a residual mWOM balance from an earlier rounding sits in the contract, so that `obtainedmWomAmount` diverges from `IERC20(mWom).balanceOf(address(this))`, the invariant that protocol fee conversion must not be exposed to a price the harvest caller can set is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert is on the protocol harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: protocol fee conversion must not be exposed to a price the harvest caller can set; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under a residual mWOM balance from an earlier rounding sits in the contract, asserting on every row that protocol fee conversion must not be exposed to a price the harvest caller can set.
