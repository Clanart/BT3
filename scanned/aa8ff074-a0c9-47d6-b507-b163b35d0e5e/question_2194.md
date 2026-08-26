# Q2194: MasterMagpie.massUpdatePools - massUpdatePools reachable by anyone while paused state flips

## Question
In rewards/MasterMagpie.sol, massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Starting from a state where the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, can an unprivileged EOA use `massUpdatePools()` to leave `totalAllocPoint` inconsistent with `tokenToPoolInfo[_stakingToken].allocPoint`, violating the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `massUpdatePools()` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `massUpdatePools()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the block in which every registered pool is rolled forward at once
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, call `massUpdatePools()`, and assert `totalAllocPoint` equals `tokenToPoolInfo[_stakingToken].allocPoint` and that no account can withdraw more than it put in.
