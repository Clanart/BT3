# Q5848: MasterMagpie.withdraw - massUpdatePools reachable by anyone while paused state flips

## Question
In rewards/MasterMagpie.sol, massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Starting from a state where the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, can an unprivileged EOA use `withdraw(address _stakingToken, uint256 _amount)` to leave `userInfo[_stakingToken][user].available` inconsistent with `userInfo[_stakingToken][user].amount`, violating the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(address _stakingToken, uint256 _amount)` sequence atomically under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, asserting at the end that `userInfo[_stakingToken][user].available` still equals `userInfo[_stakingToken][user].amount` and the PoC's balance delta is non-positive.
