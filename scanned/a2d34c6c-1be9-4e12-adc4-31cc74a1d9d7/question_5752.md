# Q5752: MasterMagpie.updatePool - massUpdatePools reachable by anyone while paused state flips

## Question
In rewards/MasterMagpie.sol, massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Can an unprivileged attacker reach this through `updatePool(address _stakingToken)` while the contract is paused so only emergencyWithdraw is reachable, and drive `_calLpSupply(_stakingToken)` out of agreement with `IERC20(_stakingToken).balanceOf(masterMagpie)` - breaking the invariant that no external actor may choose the accrual checkpoints that price other users' deposits - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updatePool(address _stakingToken)` sequence atomically under the contract is paused so only emergencyWithdraw is reachable, asserting at the end that `_calLpSupply(_stakingToken)` still equals `IERC20(_stakingToken).balanceOf(masterMagpie)` and the PoC's balance delta is non-positive.
