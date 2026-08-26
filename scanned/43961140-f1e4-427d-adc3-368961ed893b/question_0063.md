# Q0063: MasterMagpie.deposit - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. With _stakingToken, _amount, and the ERC20 the pool was registered with under attacker control and the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, can an unprivileged caller sequence `deposit(address _stakingToken, uint256 _amount)` so that `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` no longer reconcile, violating the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `deposit(address _stakingToken, uint256 _amount)` sequence atomically under the pool is the only one with a non-zero allocPoint so the whole mgpPerSec stream lands on it, asserting at the end that `mgpPerSec` still equals `IERC20(mgp).balanceOf(masterMagpie)` and the PoC's balance delta is non-positive.
