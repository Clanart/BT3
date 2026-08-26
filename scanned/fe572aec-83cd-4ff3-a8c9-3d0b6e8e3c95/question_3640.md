# Q3640: WombatStaking.harvest - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
In wombat/WombatStaking.sol, SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Does `harvest(address _lpToken)` let an unprivileged caller exploit that under the pool is marked isPoolFeeFree so the fee loop is skipped entirely, so that `totalAccumulated in mWOM` diverges from `veWom balance of WombatStaking`, the invariant that a manipulable external price must not be able to block principal deposits and withdrawals is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: the pool is marked isPoolFeeFree so the fee loop is skipped entirely.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `harvest(address _lpToken)`: constrain the setup so that the pool is marked isPoolFeeFree so the fee loop is skipped entirely, fuzz the attacker inputs (_lpToken and the timing of every harvest-driven fee split), and assert after every call that a manipulable external price must not be able to block principal deposits and withdrawals.
