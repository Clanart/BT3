# Q0318: WombatStaking.harvest - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
In wombat/WombatStaking.sol, SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Starting from a state where the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, can an unprivileged EOA use `harvest(address _lpToken)` to leave `womRewards measured by balance delta` inconsistent with `the amount queued into poolInfo.rewarder`, violating the invariant that a manipulable external price must not be able to block principal deposits and withdrawals and extracting High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: the contract is holding WOM that mWOM._convert has just transferred in but not yet locked.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the contract is holding WOM that mWOM._convert has just transferred in but not yet locked, snapshot `womRewards measured by balance delta` and `the amount queued into poolInfo.rewarder`, run the attacker's `harvest(address _lpToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
