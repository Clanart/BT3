# Q3880: WombatStaking.withdraw - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
wombat/WombatStaking.sol: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, is there an unprivileged sequence of `withdraw(address,uint256,uint256,address) via a pool helper` that leaves `feeInfos[i].value` unreconciled with `totalFee`, violates the invariant that a manipulable external price must not be able to block principal deposits and withdrawals, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(address,uint256,uint256,address) via a pool helper` sequence atomically under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, asserting at the end that `feeInfos[i].value` still equals `totalFee` and the PoC's balance delta is non-positive.
