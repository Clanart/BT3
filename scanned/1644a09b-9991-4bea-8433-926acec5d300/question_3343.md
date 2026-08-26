# Q3343: SmartWomConvert.smartConvert - smartConvert prices itself from live pool state

## Question
wombat/SmartWomConvert.sol: smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. With _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from under attacker control and the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, can an unprivileged caller sequence `smartConvert(uint256 _amountIn, uint256 _mode)` so that `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` no longer reconcile, violating the invariant that the split between minting and buying back must not be settable by a party who can move the pool in the same block and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert prices itself from live pool state)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Precondition: the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two.
- Invariant to test: the split between minting and buying back must not be settable by a party who can move the pool in the same block; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the call arrives from ManualCompound.compound with a caller-supplied _convertRatio and _minRec and _mode two, call `smartConvert(uint256 _amountIn, uint256 _mode)`, and assert `maxSwapAmount()` equals `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and that no account can withdraw more than it put in.
