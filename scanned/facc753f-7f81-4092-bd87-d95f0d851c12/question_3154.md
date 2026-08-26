# Q3154: WombatStaking.harvest - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
wombat/WombatStaking.sol: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Under the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, is there an unprivileged sequence of `harvest(address _lpToken)` that leaves `IMintableERC20(poolInfo.receiptToken).totalSupply()` unreconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`, violates the invariant that a manipulable external price must not be able to block principal deposits and withdrawals, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, have the attacker run `harvest(address _lpToken)`, then assert the victim's claimable value and the `IMintableERC20(poolInfo.receiptToken).totalSupply()` versus `IMasterWombat(masterWombat) staked balance for poolInfo.pid` relation are unchanged by the attacker's transaction.
