# Q2919: VLMGP.startUnlock - vote commitment blocks the exit forever

## Question
VLMGP.sol: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. With _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot under attacker control and the pool the attacker voted for has since been deactivated so unvote reverts, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` no longer reconcile, violating the invariant that a user must always retain a path to release their vote commitment and exit their lock and realising Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: vote commitment blocks the exit forever)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: a user must always retain a path to release their vote commitment and exit their lock; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot) under the pool the attacker voted for has since been deactivated so unvote reverts, asserting on every row that a user must always retain a path to release their vote commitment and exit their lock.
