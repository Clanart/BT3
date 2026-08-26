# Q4277: SmartWomConvert.smartConvert - smartConvert prices itself from live pool state

## Question
wombat/SmartWomConvert.sol: smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Under a residual mWOM balance from an earlier rounding sits in the contract, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `_minRec` unreconciled with `convertAmount + amountRec`, violates the invariant that the split between minting and buying back must not be settable by a party who can move the pool in the same block, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert prices itself from live pool state)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: the split between minting and buying back must not be settable by a party who can move the pool in the same block; concretely, `_minRec` must stay reconciled with `convertAmount + amountRec`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under a residual mWOM balance from an earlier rounding sits in the contract, asserting on every row that the split between minting and buying back must not be settable by a party who can move the pool in the same block.
