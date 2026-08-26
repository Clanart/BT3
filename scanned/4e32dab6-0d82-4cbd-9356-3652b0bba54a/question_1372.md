# Q1372: MasterMagpie.withdraw - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, is there an unprivileged sequence of `withdraw(address _stakingToken, uint256 _amount)` that leaves `userInfo[_stakingToken][user].available` unreconciled with `userInfo[_stakingToken][user].amount`, violates the invariant that no external actor may choose the accrual checkpoints that price other users' deposits, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `withdraw(address _stakingToken, uint256 _amount)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address _stakingToken, uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken, _amount, and withdraw ordering inside a block
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, have the attacker run `withdraw(address _stakingToken, uint256 _amount)`, then assert the victim's claimable value and the `userInfo[_stakingToken][user].available` versus `userInfo[_stakingToken][user].amount` relation are unchanged by the attacker's transaction.
