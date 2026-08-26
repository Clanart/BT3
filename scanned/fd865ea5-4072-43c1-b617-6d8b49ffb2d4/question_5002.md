# Q5002: SmartWomConvert.smartConvert - smartConvert prices itself from live pool state

## Question
wombat/SmartWomConvert.sol - smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Can an unprivileged attacker controlling _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from, under the router leaves a non-zero allowance after the swap, exploit this through `smartConvert(uint256 _amountIn, uint256 _mode)` to break the reconciliation between `_convertRatio` and `DENOMINATOR` and the invariant that the split between minting and buying back must not be settable by a party who can move the pool in the same block, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert prices itself from live pool state)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() reads currentRatio() and maxSwapAmount() straight from the Wombat wom/mWom pool in the same transaction, so an attacker who moves that pool immediately before the call decides how much of the input is swapped rather than minted. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: the split between minting and buying back must not be settable by a party who can move the pool in the same block; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the router leaves a non-zero allowance after the swap, have the attacker run `smartConvert(uint256 _amountIn, uint256 _mode)`, then assert the victim's claimable value and the `_convertRatio` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
