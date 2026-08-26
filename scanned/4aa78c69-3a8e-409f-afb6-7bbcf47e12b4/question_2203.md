# Q2203: SmartWomConvert.smartConvert - smartConvert reverts under manipulation and blocks the harvest path

## Question
wombat/SmartWomConvert.sol: smartConvert() passes _minRec equal to _amountIn, so if the buyback leg returns less than one-to-one the whole call reverts, and because it sits inside _toMasterWomAndSendReward that revert propagates to every deposit, depositLP and withdraw on the pool. Under womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `maxSwapAmount()` unreconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`, violates the invariant that a manipulable external price must not be able to block principal deposits and withdrawals, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert reverts under manipulation and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() passes _minRec equal to _amountIn, so if the buyback leg returns less than one-to-one the whole call reverts, and because it sits inside _toMasterWomAndSendReward that revert propagates to every deposit, depositLP and withdraw on the pool. Precondition: womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `maxSwapAmount()` must stay reconciled with `IAsset(womAsset).cash() and IAsset(womAsset).liability()`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `smartConvert(uint256 _amountIn, uint256 _mode)`: constrain the setup so that womCash exceeds womLiability so maxSwapAmount returns zero and convertRatio stays at DENOMINATOR, fuzz the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from), and assert after every call that a manipulable external price must not be able to block principal deposits and withdrawals.
