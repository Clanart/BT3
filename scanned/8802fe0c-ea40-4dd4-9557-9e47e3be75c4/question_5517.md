# Q5517: WombatStaking.harvest - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
Consider wombat/WombatStaking.sol, where SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Assuming the veWOM contract leaves a non-zero allowance after mint, can an unprivileged attacker turn this into a divergence between `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token` via `harvest(address _lpToken)`, breaking the invariant that a manipulable external price must not be able to block principal deposits and withdrawals and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: the veWOM contract leaves a non-zero allowance after mint.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `harvest(address _lpToken)`: constrain the setup so that the veWOM contract leaves a non-zero allowance after mint, fuzz the attacker inputs (_lpToken and the timing of every harvest-driven fee split), and assert after every call that a manipulable external price must not be able to block principal deposits and withdrawals.
