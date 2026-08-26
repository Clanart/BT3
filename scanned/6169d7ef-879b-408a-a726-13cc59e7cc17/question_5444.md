# Q5444: MasterMagpie.updatePool - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol - massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Can an unprivileged attacker controlling _stakingToken and the timestamp at which accMGPPerShare is rolled forward, under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), exploit this through `updatePool(address _stakingToken)` to break the reconciliation between `userInfo[_stakingToken][user].rewardDebt` and `tokenToPoolInfo[_stakingToken].accMGPPerShare` and the invariant that no external actor may choose the accrual checkpoints that price other users' deposits, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updatePool(address _stakingToken)`: constrain the setup so that the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), fuzz the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward), and assert after every call that no external actor may choose the accrual checkpoints that price other users' deposits.
