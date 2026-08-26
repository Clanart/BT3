# Q3360: SmartWomConvert.smartConvert - smartConvert is on the protocol harvest path

## Question
In wombat/SmartWomConvert.sol, WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Starting from a state where the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, can an unprivileged EOA use `smartConvert(uint256 _amountIn, uint256 _mode)` to leave `amountRec from swapExactTokensForTokens` inconsistent with `convertAmount minted 1:1 by IMWom(mWom).deposit`, violating the invariant that protocol fee conversion must not be exposed to a price the harvest caller can set and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert is on the protocol harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: WombatStaking._sendRewards calls IConverter(smartWomConverter).smartConvert(feeAmount, 0) for the mWOM-flagged fee leg, so the manipulable price applies to protocol fee value, not just to the caller's own funds. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: protocol fee conversion must not be exposed to a price the harvest caller can set; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, have the attacker run `smartConvert(uint256 _amountIn, uint256 _mode)`, then assert the victim's claimable value and the `amountRec from swapExactTokensForTokens` versus `convertAmount minted 1:1 by IMWom(mWom).deposit` relation are unchanged by the attacker's transaction.
