# Q5179: MasterMagpie.massUpdatePools - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol - massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Can an unprivileged attacker controlling the block in which every registered pool is rolled forward at once, under the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, exploit this through `massUpdatePools()` to break the reconciliation between `userInfo[_stakingToken][user].rewardDebt` and `tokenToPoolInfo[_stakingToken].accMGPPerShare` and the invariant that no external actor may choose the accrual checkpoints that price other users' deposits, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `massUpdatePools()` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `massUpdatePools()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the block in which every registered pool is rolled forward at once
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `massUpdatePools()`: constrain the setup so that the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, fuzz the attacker inputs (the block in which every registered pool is rolled forward at once), and assert after every call that no external actor may choose the accrual checkpoints that price other users' deposits.
