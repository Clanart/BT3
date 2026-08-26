# Q4923: WombatStaking.deposit - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
wombat/WombatStaking.sol: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. With _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper under attacker control and the attacker deposits and withdraws through the same helper inside one transaction, can an unprivileged caller sequence `deposit(address,uint256,uint256,address,address) via a pool helper` so that `isPoolFeeFree[_lpToken]` and `feeInfos.length` no longer reconcile, violating the invariant that a manipulable external price must not be able to block principal deposits and withdrawals and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: the attacker deposits and withdraws through the same helper inside one transaction.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the attacker deposits and withdraws through the same helper inside one transaction, snapshot `isPoolFeeFree[_lpToken]` and `feeInfos.length`, run the attacker's `deposit(address,uint256,uint256,address,address) via a pool helper` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
