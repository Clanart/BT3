# Q5229: WombatStaking.withdraw - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
wombat/WombatStaking.sol - SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Can an unprivileged attacker controlling _liquidity and _minAmount, forwarded verbatim from the helper's withdraw, under a large honest deposit is pending in the mempool for the same pool, exploit this through `withdraw(address,uint256,uint256,address) via a pool helper` to break the reconciliation between `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` and `_liquidity burned from the receipt token` and the invariant that a manipulable external price must not be able to block principal deposits and withdrawals, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: a large honest deposit is pending in the mempool for the same pool.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw` must stay reconciled with `_liquidity burned from the receipt token`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw) under a large honest deposit is pending in the mempool for the same pool, asserting on every row that a manipulable external price must not be able to block principal deposits and withdrawals.
