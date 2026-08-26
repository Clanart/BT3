# Q4092: WombatStaking.harvest - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
wombat/WombatStaking.sol: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. With _lpToken and the timing of every harvest-driven fee split under attacker control and several feeInfos entries are active at once and the harvested amount is small, can an unprivileged caller sequence `harvest(address _lpToken)` so that `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` no longer reconcile, violating the invariant that a manipulable external price must not be able to block principal deposits and withdrawals and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up several feeInfos entries are active at once and the harvested amount is small, snapshot `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM`, run the attacker's `harvest(address _lpToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
