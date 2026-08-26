# Q1290: MasterMagpie.deposit - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol - massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Can an unprivileged attacker controlling _stakingToken, _amount, and the ERC20 the pool was registered with, under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, exploit this through `deposit(address _stakingToken, uint256 _amount)` to break the reconciliation between `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` and the invariant that no external actor may choose the accrual checkpoints that price other users' deposits, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, snapshot `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)`, run the attacker's `deposit(address _stakingToken, uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
