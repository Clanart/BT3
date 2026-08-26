# Q3665: MasterMagpie.updatePool - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. With _stakingToken and the timestamp at which accMGPPerShare is rolled forward under attacker control and a large honest deposit is sitting in the mempool and the attacker sandwiches it, can an unprivileged caller sequence `updatePool(address _stakingToken)` so that `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount` no longer reconcile, violating the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updatePool(address _stakingToken)` sequence atomically under a large honest deposit is sitting in the mempool and the attacker sandwiches it, asserting at the end that `vlmgp.totalSupply()` still equals `sum of userInfo[vlmgp][*].amount` and the PoC's balance delta is non-positive.
