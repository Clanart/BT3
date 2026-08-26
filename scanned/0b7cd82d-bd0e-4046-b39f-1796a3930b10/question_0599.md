# Q0599: SmartWomConvert.smartConvert - smartConvert reverts under manipulation and blocks the harvest path

## Question
In wombat/SmartWomConvert.sol, smartConvert() passes _minRec equal to _amountIn, so if the buyback leg returns less than one-to-one the whole call reverts, and because it sits inside _toMasterWomAndSendReward that revert propagates to every deposit, depositLP and withdraw on the pool. Starting from a state where the attacker has pushed mWom below buybackThreshold against wom in the same transaction, can an unprivileged EOA use `smartConvert(uint256 _amountIn, uint256 _mode)` to leave `_convertRatio` inconsistent with `DENOMINATOR`, violating the invariant that a manipulable external price must not be able to block principal deposits and withdrawals and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert reverts under manipulation and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() passes _minRec equal to _amountIn, so if the buyback leg returns less than one-to-one the whole call reverts, and because it sits inside _toMasterWomAndSendReward that revert propagates to every deposit, depositLP and withdraw on the pool. Precondition: the attacker has pushed mWom below buybackThreshold against wom in the same transaction.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `_convertRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from) under the attacker has pushed mWom below buybackThreshold against wom in the same transaction, asserting on every row that a manipulable external price must not be able to block principal deposits and withdrawals.
