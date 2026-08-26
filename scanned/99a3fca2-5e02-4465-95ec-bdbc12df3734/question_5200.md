# Q5200: MasterMagpie.deposit - massUpdatePools reachable by anyone while paused state flips

## Question
Consider rewards/MasterMagpie.sol, where massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Assuming the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), can an unprivileged attacker turn this into a divergence between `IBaseRewardPool(rewarder).balanceOf(user)` and `IBaseRewardPool(rewarder).totalStaked()` via `deposit(address _stakingToken, uint256 _amount)`, breaking the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), then assert `IBaseRewardPool(rewarder).balanceOf(user)` and `IBaseRewardPool(rewarder).totalStaked()` end identical in both runs.
