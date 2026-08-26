# Q5251: SmartWomConvert.smartConvert - smartConvert reverts under manipulation and blocks the harvest path

## Question
wombat/SmartWomConvert.sol: smartConvert() passes _minRec equal to _amountIn, so if the buyback leg returns less than one-to-one the whole call reverts, and because it sits inside _toMasterWomAndSendReward that revert propagates to every deposit, depositLP and withdraw on the pool. Under _convertRatio is set to zero so the entire input goes through the AMM, is there an unprivileged sequence of `smartConvert(uint256 _amountIn, uint256 _mode)` that leaves `amountRec from swapExactTokensForTokens` unreconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`, violates the invariant that a manipulable external price must not be able to block principal deposits and withdrawals, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert reverts under manipulation and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() passes _minRec equal to _amountIn, so if the buyback leg returns less than one-to-one the whole call reverts, and because it sits inside _toMasterWomAndSendReward that revert propagates to every deposit, depositLP and withdraw on the pool. Precondition: _convertRatio is set to zero so the entire input goes through the AMM.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish _convertRatio is set to zero so the entire input goes through the AMM, have the attacker run `smartConvert(uint256 _amountIn, uint256 _mode)`, then assert the victim's claimable value and the `amountRec from swapExactTokensForTokens` versus `convertAmount minted 1:1 by IMWom(mWom).deposit` relation are unchanged by the attacker's transaction.
