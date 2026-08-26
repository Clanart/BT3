# Q5673: MasterMagpie.withdraw - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol - massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Can an unprivileged attacker controlling _stakingToken, _amount, and withdraw ordering inside a block, under the contract is paused so only emergencyWithdraw is reachable, exploit this through `withdraw(address _stakingToken, uint256 _amount)` to break the reconciliation between `mgpPerSec` and `IERC20(mgp).balanceOf(masterMagpie)` and the invariant that no external actor may choose the accrual checkpoints that price other users' deposits, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `withdraw(address _stakingToken, uint256 _amount)`: constrain the setup so that the contract is paused so only emergencyWithdraw is reachable, fuzz the attacker inputs (_stakingToken, _amount, and withdraw ordering inside a block), and assert after every call that no external actor may choose the accrual checkpoints that price other users' deposits.
