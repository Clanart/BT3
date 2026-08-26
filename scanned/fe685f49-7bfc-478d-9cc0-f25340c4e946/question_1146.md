# Q1146: MasterMagpie.updatePool - massUpdatePools reachable by anyone while paused state flips

## Question
Consider rewards/MasterMagpie.sol, where massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Assuming the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, can an unprivileged attacker turn this into a divergence between `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp` via `updatePool(address _stakingToken)`, breaking the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updatePool(address _stakingToken)`: constrain the setup so that the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, fuzz the attacker inputs (_stakingToken and the timestamp at which accMGPPerShare is rolled forward), and assert after every call that no external actor may choose the accrual checkpoints that price other users' deposits.
