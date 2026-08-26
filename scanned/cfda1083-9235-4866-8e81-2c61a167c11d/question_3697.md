# Q3697: MasterMagpie.massUpdatePools - massUpdatePools reachable by anyone while paused state flips

## Question
Consider rewards/MasterMagpie.sol, where massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Assuming a large honest deposit is sitting in the mempool and the attacker sandwiches it, can an unprivileged attacker turn this into a divergence between `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` via `massUpdatePools()`, breaking the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `massUpdatePools()` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `massUpdatePools()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the block in which every registered pool is rolled forward at once
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large honest deposit is sitting in the mempool and the attacker sandwiches it, call `massUpdatePools()`, and assert `mgpPerSec` equals `IERC20(mgp).balanceOf(masterMagpie)` and that no account can withdraw more than it put in.
