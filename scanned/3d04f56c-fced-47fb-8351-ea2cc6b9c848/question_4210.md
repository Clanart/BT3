# Q4210: WombatStaking.deposit - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
In wombat/WombatStaking.sol, SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Can an unprivileged attacker reach this through `deposit(address,uint256,uint256,address,address) via a pool helper` while several feeInfos entries are active at once and the harvested amount is small, and drive `feeInfos[i].value` out of agreement with `totalFee` - breaking the invariant that a manipulable external price must not be able to block principal deposits and withdrawals - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper) under several feeInfos entries are active at once and the harvested amount is small, asserting on every row that a manipulable external price must not be able to block principal deposits and withdrawals.
