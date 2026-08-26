# Q5024: SmartWomConvert.smartConvert - smartConvert reverts under manipulation and blocks the harvest path

## Question
wombat/SmartWomConvert.sol - smartConvert() passes _minRec equal to _amountIn, so if the buyback leg returns less than one-to-one the whole call reverts, and because it sits inside _toMasterWomAndSendReward that revert propagates to every deposit, depositLP and withdraw on the pool. Can an unprivileged attacker controlling _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from, under the router leaves a non-zero allowance after the swap, exploit this through `smartConvert(uint256 _amountIn, uint256 _mode)` to break the reconciliation between `maxSwapAmount()` and `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and the invariant that a manipulable external price must not be able to block principal deposits and withdrawals, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert reverts under manipulation and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() passes _minRec equal to _amountIn, so if the buyback leg returns less than one-to-one the whole call reverts, and because it sits inside _toMasterWomAndSendReward that revert propagates to every deposit, depositLP and withdraw on the pool. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the router leaves a non-zero allowance after the swap, call `smartConvert(uint256 _amountIn, uint256 _mode)`, and assert `maxSwapAmount()` equals `IAsset(womAsset).cash() and IAsset(womAsset).liability()` and that no account can withdraw more than it put in.
