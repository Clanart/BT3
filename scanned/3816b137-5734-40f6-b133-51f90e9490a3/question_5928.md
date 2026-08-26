# Q5928: MasterMagpie.withdraw - massUpdatePools reachable by anyone while paused state flips

## Question
In rewards/MasterMagpie.sol, massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Starting from a state where the attacker repeats the call in the same block to observe the second, no-op iteration, can an unprivileged EOA use `withdraw(address _stakingToken, uint256 _amount)` to leave `userInfo[_stakingToken][user].rewardDebt` inconsistent with `tokenToPoolInfo[_stakingToken].accMGPPerShare`, violating the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker repeats the call in the same block to observe the second, no-op iteration, call `withdraw(address _stakingToken, uint256 _amount)`, and assert `userInfo[_stakingToken][user].rewardDebt` equals `tokenToPoolInfo[_stakingToken].accMGPPerShare` and that no account can withdraw more than it put in.
