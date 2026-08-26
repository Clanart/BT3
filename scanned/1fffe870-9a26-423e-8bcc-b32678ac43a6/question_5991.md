# Q5991: MasterMagpie.deposit - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. With _stakingToken, _amount, and the ERC20 the pool was registered with under attacker control and the attacker splits the action across two transactions in the same block with a flash-loaned staking token, can an unprivileged caller sequence `deposit(address _stakingToken, uint256 _amount)` so that `userInfo[_stakingToken][user].rewardDebt` and `tokenToPoolInfo[_stakingToken].accMGPPerShare` no longer reconcile, violating the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the attacker splits the action across two transactions in the same block with a flash-loaned staking token.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `deposit(address _stakingToken, uint256 _amount)`: constrain the setup so that the attacker splits the action across two transactions in the same block with a flash-loaned staking token, fuzz the attacker inputs (_stakingToken, _amount, and the ERC20 the pool was registered with), and assert after every call that no external actor may choose the accrual checkpoints that price other users' deposits.
