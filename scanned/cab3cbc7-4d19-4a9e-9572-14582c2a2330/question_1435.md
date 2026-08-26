# Q1435: SmartWomConvert.smartConvert - smartConvert prices itself from live pool state

## Question
In wombat/SmartWomConvert.sol, smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Does `smartConvert(uint256 _amountIn, uint256 _mode)` let an unprivileged caller exploit that under the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, so that `obtainedmWomAmount` diverges from `IERC20(mWom).balanceOf(address(this))`, the invariant that the split between minting and buying back must not be settable by a party who can move the pool in the same block is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert prices itself from live pool state)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Precondition: the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs.
- Invariant to test: the split between minting and buying back must not be settable by a party who can move the pool in the same block; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has pushed mWom above the peg so currentRatio exceeds buybackThreshold and no swap leg runs, call `smartConvert(uint256 _amountIn, uint256 _mode)`, and assert `obtainedmWomAmount` equals `IERC20(mWom).balanceOf(address(this))` and that no account can withdraw more than it put in.
