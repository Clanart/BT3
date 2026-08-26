# Q4914: SmartWomConvert.convertFor - obtained amount computed from arithmetic rather than from balance delta

## Question
Note that in wombat/SmartWomConvert.sol, _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Can an attacker holding only tokens bought on market reach it via `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` under the router leaves a non-zero allowance after the swap and force `maxSwapAmount()` apart from `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, breaking the invariant that the amount credited to a user must equal the balance the contract actually received for them for Critical - Direct theft of user funds?

## Target
- File/function: wombat/SmartWomConvert.sol -> `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` (mechanism: obtained amount computed from arithmetic rather than from balance delta)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for, _convertRatio, _minRec and _mode, reachable directly and through ManualCompound.compound
- Exploit idea: _convertFor() sets obtainedmWomAmount = convertAmount + amountRec instead of measuring the mWOM balance delta, so any discrepancy between the router's reported output and the tokens actually received is absorbed by the contract's shared balance. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: the amount credited to a user must equal the balance the contract actually received for them; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)` sequence atomically under the router leaves a non-zero allowance after the swap, asserting at the end that `maxSwapAmount()` still equals `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and the PoC's balance delta is non-positive.
