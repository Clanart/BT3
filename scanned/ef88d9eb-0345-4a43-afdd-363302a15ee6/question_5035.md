# Q5035: SmartWomConvert.smartConvert - obtained amount computed from arithmetic rather than from balance delta

## Question
In wombat/SmartWomConvert.sol, _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Can an unprivileged attacker reach this through `smartConvert(uint256 _amountIn, uint256 _mode)` while the router leaves a non-zero allowance after the swap, and drive `amountRec from swapExactTokensForTokens` out of agreement with `convertAmount minted 1:1 by IMWom(mWom).deposit` - breaking the invariant that the amount credited to a user must equal the balance the contract actually received for them - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under the router leaves a non-zero allowance after the swap, asserting on every row that the amount credited to a user must equal the balance the contract actually received for them.
