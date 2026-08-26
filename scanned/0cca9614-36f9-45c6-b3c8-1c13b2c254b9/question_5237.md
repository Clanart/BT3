# Q5237: SmartWomConvert.smartConvert - smartConvert prices itself from live pool state

## Question
wombat/SmartWomConvert.sol: smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Under _convertRatio is set to zero so the entire input goes through the AMM, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `currentRatio()` unreconciled with `buybackThreshold`, violates the invariant that the split between minting and buying back must not be settable by a party who can move the pool in the same block, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert prices itself from live pool state)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: the split between minting and buying back must not be settable by a party who can move the pool in the same block; concretely, `currentRatio()` must stay reconciled with `buybackThreshold`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under _convertRatio is set to zero so the entire input goes through the AMM, then assert `currentRatio()` and `buybackThreshold` end identical in both runs.
