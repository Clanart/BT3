# Q2263: MasterMagpie.deposit - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. With _stakingToken, _amount, and the ERC20 the pool was registered with under attacker control and the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, can an unprivileged caller sequence `deposit(address _stakingToken, uint256 _amount)` so that `userInfo[_stakingToken][user].available` and `userInfo[_stakingToken][user].amount` no longer reconcile, violating the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, call `deposit(address _stakingToken, uint256 _amount)`, and assert `userInfo[_stakingToken][user].available` equals `userInfo[_stakingToken][user].amount` and that no account can withdraw more than it put in.
