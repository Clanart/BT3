# Q2611: WombatStaking.harvest - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
In wombat/WombatStaking.sol, SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Can an unprivileged attacker reach this through `harvest(address _lpToken)` while smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, and drive `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` out of agreement with `_liquidity burned from the receipt token` - breaking the invariant that a manipulable external price must not be able to block principal deposits and withdrawals - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, snapshot `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token`, run the attacker's `harvest(address _lpToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
