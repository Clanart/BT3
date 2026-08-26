# Q5762: MasterMagpie.deposit - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol - massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Can an unprivileged attacker controlling _stakingToken, _amount, and the ERC20 the pool was registered with, under the victim has a large unClaimedMgp balance that has not been settled for several epochs, exploit this through `deposit(address _stakingToken, uint256 _amount)` to break the reconciliation between `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` and the invariant that no external actor may choose the accrual checkpoints that price other users' deposits, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `deposit(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and the ERC20 the pool was registered with
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unClaimedMgp balance that has not been settled for several epochs, call `deposit(address _stakingToken, uint256 _amount)`, and assert `mgpPerSec` equals `IERC20(mgp).balanceOf(masterMagpie)` and that no account can withdraw more than it put in.
