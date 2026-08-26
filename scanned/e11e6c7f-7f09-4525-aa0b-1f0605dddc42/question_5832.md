# Q5832: MasterMagpie.updatePool - massUpdatePools reachable by anyone while paused state flips

## Question
In rewards/MasterMagpie.sol, massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Can an unprivileged attacker reach this through `updatePool(address _stakingToken)` while the victim has a large unClaimedMgp balance that has not been settled for several epochs, and drive `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` out of agreement with `block.timestamp` - breaking the invariant that no external actor may choose the accrual checkpoints that price other users' deposits - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unClaimedMgp balance that has not been settled for several epochs, call `updatePool(address _stakingToken)`, and assert `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` equals `block.timestamp` and that no account can withdraw more than it put in.
