# Q0568: SmartWomConvert.smartConvert - smartConvert is on the protocol harvest path

## Question
In wombat/SmartWomConvert.sol, WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Starting from a state where the attacker has pushed mWom below buybackThreshold against wom in the same transaction, can an unprivileged EOA use `smartConvert(uint256 _amountIn, uint256 _mode)` to leave `obtainedmWomAmount` inconsistent with `IERC20(mWom).balanceOf(address(this))`, violating the invariant that protocol fee conversion must not be exposed to a price the harvest caller can set and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert is on the protocol harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Precondition: the attacker has pushed mWom below buybackThreshold against wom in the same transaction.
- Invariant to test: protocol fee conversion must not be exposed to a price the harvest caller can set; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `smartConvert(uint256 _amountIn, uint256 _mode)`: constrain the setup so that the attacker has pushed mWom below buybackThreshold against wom in the same transaction, fuzz the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from), and assert after every call that protocol fee conversion must not be exposed to a price the harvest caller can set.
