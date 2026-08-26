# Q4584: WombatStaking.deposit - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
Note that in wombat/WombatStaking.sol, SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Can an attacker holding only tokens bought on market reach it via `deposit(address,uint256,uint256,address,address) via a pool helper` under the deposit token for the pool is wBNB and the helper arrived through depositNative and force `womRewards measured by balance delta` apart from `the amount queued into poolInfo.rewarder`, breaking the invariant that a manipulable external price must not be able to block principal deposits and withdrawals for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: the deposit token for the pool is wBNB and the helper arrived through depositNative.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the deposit token for the pool is wBNB and the helper arrived through depositNative, then assert `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` end identical in both runs.
