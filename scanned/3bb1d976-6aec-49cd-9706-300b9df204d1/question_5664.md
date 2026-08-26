# Q5664: MasterMagpie.deposit - massUpdatePools reachable by anyone while paused state flips

## Question
Consider rewards/MasterMagpie.sol, where massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Assuming the contract is paused so only emergencyWithdraw is reachable, can an unprivileged attacker turn this into a divergence between `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount` via `deposit(address _stakingToken, uint256 _amount)`, breaking the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the contract is paused so only emergencyWithdraw is reachable, snapshot `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount`, run the attacker's `deposit(address _stakingToken, uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
