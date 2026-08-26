# Q1241: WombatStaking.harvest - reward amounts measured by balance delta across the same fee loop

## Question
wombat/WombatStaking.sol: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Under the contract is holding WOM collected as a protocol fee that has not yet been split, is there an unprivileged sequence of `harvest(address _lpToken)` that leaves `IERC20(poolInfo.lpAddress).balanceOf(address(this))` unreconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`, violates the invariant that harvested reward measurement must be isolated from the fee movements that consume it, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract is holding WOM collected as a protocol fee that has not yet been split, call `harvest(address _lpToken)`, and assert `IERC20(poolInfo.lpAddress).balanceOf(address(this))` equals `lpReceived credited by IMintableERC20(receiptToken).mint` and that no account can withdraw more than it put in.
