# Q2631: WombatStaking.harvest - reward amounts measured by balance delta across the same fee loop

## Question
In wombat/WombatStaking.sol, _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Does `harvest(address _lpToken)` let an unprivileged caller exploit that under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, so that `IMintableERC20(poolInfo.receiptToken).totalSupply()` diverges from `IMasterWombat(masterWombat) staked balance for poolInfo.pid`, the invariant that harvested reward measurement must be isolated from the fee movements that consume it is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: reward amounts measured by balance delta across the same fee loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _toMasterWomAndSendReward() computes womRewards and every bonus amount as a balance delta around the MasterWombat call, while the fee loop inside _sendRewards moves those same tokens, so any token that is both a bonus reward and a fee currency is measured against a moving balance. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: harvested reward measurement must be isolated from the fee movements that consume it; concretely, `IMintableERC20(poolInfo.receiptToken).totalSupply()` must stay reconciled with `IMasterWombat(masterWombat) staked balance for poolInfo.pid`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, snapshot `IMintableERC20(poolInfo.receiptToken).totalSupply()` and `IMasterWombat(masterWombat) staked balance for poolInfo.pid`, run the attacker's `harvest(address _lpToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
