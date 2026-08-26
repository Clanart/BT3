# Q4301: WombatStaking.withdraw - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
wombat/WombatStaking.sol - SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Can an unprivileged attacker controlling _liquidity and _minAmount, forwarded verbatim from the helper's withdraw, under several feeInfos entries are active at once and the harvested amount is small, exploit this through `withdraw(address,uint256,uint256,address) via a pool helper` to break the reconciliation between `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder` and the invariant that a manipulable external price must not be able to block principal deposits and withdrawals, yielding High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, forwarded verbatim from the helper's withdraw) under several feeInfos entries are active at once and the harvested amount is small, asserting on every row that a manipulable external price must not be able to block principal deposits and withdrawals.
