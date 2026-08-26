# Q3866: SmartWomConvert.smartConvert - smartConvert reverts under manipulation and blocks the harvest path

## Question
Note that in wombat/SmartWomConvert.sol, smartConvert() passes _minRec equal to _amountIn, so if the buyback leg returns less than one-to-one the whole call reverts, and because it sits inside _toMasterWomAndSendReward that revert propagates to every deposit, depositLP and withdraw on the pool. Can an attacker holding only tokens bought on market reach it via `smartConvert(uint256 _amountIn, uint256 _mode)` under the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero and force `obtainedmWomAmount` apart from `IERC20(mWom).balanceOf(address(this))`, breaking the invariant that a manipulable external price must not be able to block principal deposits and withdrawals for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: smartConvert reverts under manipulation and blocks the harvest path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: smartConvert() passes _minRec equal to _amountIn, so if the buyback leg returns less than one-to-one the whole call reverts, and because it sits inside _toMasterWomAndSendReward that revert propagates to every deposit, depositLP and withdraw on the pool. Precondition: the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `obtainedmWomAmount` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `smartConvert(uint256 _amountIn, uint256 _mode)`: constrain the setup so that the call arrives from ArbWomUp3._deposit mode two with _minRec hardcoded to zero, fuzz the attacker inputs (_amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from), and assert after every call that a manipulable external price must not be able to block principal deposits and withdrawals.
