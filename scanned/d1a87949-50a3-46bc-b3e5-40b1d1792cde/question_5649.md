# Q5649: MasterMagpie.updatePool - massUpdatePools reachable by anyone while paused state flips

## Question
rewards/MasterMagpie.sol: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. With _stakingToken and the timestamp at which accMGPPerShare is rolled forward under attacker control and the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, can an unprivileged caller sequence `updatePool(address _stakingToken)` so that `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt` no longer reconcile, violating the invariant that no external actor may choose the accrual checkpoints that price other users' deposits and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `updatePool(address _stakingToken)` (mechanism: massUpdatePools reachable by anyone while paused state flips)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updatePool(address _stakingToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _stakingToken and the timestamp at which accMGPPerShare is rolled forward
- Exploit idea: massUpdatePools() and updatePool() are permissionless and only gated by whenNotPaused, so an attacker controls the exact timestamps at which every pool's accMGPPerShare is rebased relative to their own deposit and withdraw transactions. Precondition: the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp.
- Invariant to test: no external actor may choose the accrual checkpoints that price other users' deposits; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the contract has just been unpaused and lastRewardTimestamp is far behind block.timestamp, have the attacker run `updatePool(address _stakingToken)`, then assert the victim's claimable value and the `unClaimedMgp[_stakingToken][user]` versus `userInfo[_stakingToken][user].rewardDebt` relation are unchanged by the attacker's transaction.
