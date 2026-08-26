# Q4668: WombatStaking.withdraw - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
In wombat/WombatStaking.sol, SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Starting from a state where the deposit token for the pool is wBNB and the helper arrived through depositNative, can an unprivileged EOA use `withdraw(address,uint256,uint256,address) via a pool helper` to leave `isPoolFeeFree[_lpToken]` inconsistent with `feeInfos.length`, violating the invariant that a manipulable external price must not be able to block principal deposits and withdrawals and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the deposit token for the pool is wBNB and the helper arrived through depositNative, then assert `isPoolFeeFree[_lpToken]` and `feeInfos.length` end identical in both runs.
