# Q3834: SmartWomConvert.smartConvert - smartConvert prices itself from live pool state

## Question
wombat/SmartWomConvert.sol - smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Can an unprivileged attacker controlling _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from, under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, exploit this through `smartConvert(uint256 _amountIn, uint256 _mode)` to break the reconciliation between `amountRec from swapExactTokensForTokens` and `convertAmount minted 1:1 by IMWom(mWom).deposit` and the invariant that the split between minting and buying back must not be settable by a party who can move the pool in the same block, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert prices itself from live pool state)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: the split between minting and buying back must not be settable by a party who can move the pool in the same block; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, then assert `amountRec from swapExactTokensForTokens` and `convertAmount minted 1:1 by IMWom(mWom).deposit` end identical in both runs.
