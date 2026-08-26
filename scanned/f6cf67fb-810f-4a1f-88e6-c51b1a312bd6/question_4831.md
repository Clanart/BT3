# Q4831: MasterMagpie.deposit - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. With _stakingToken, _amount, and the ERC20 the pool was registered with under attacker control and the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, can an unprivileged caller sequence `deposit(address _stakingToken, uint256 _amount)` so that `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp` no longer reconcile, violating the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `deposit(address _stakingToken, uint256 _amount)` sequence atomically under the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, asserting at the end that `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` still equals `block.timestamp` and the PoC's balance delta is non-positive.
