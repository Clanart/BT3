# Q1507: VLMGP.startUnlock - vote commitment blocks the exit forever

## Question
In VLMGP.sol, startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Does `startUnlock(uint256 _amountToCoolDown)` let an unprivileged caller exploit that under coolDownInSecs is at its configured production value and endTime is far in the future, so that `userUnlockings[user][i].endTime` diverges from `block.timestamp`, the invariant that a user must always retain a path to release their vote commitment and exit their lock is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: vote commitment blocks the exit forever)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: a user must always retain a path to release their vote commitment and exit their lock; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish coolDownInSecs is at its configured production value and endTime is far in the future, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `userUnlockings[user][i].endTime` versus `block.timestamp` relation are unchanged by the attacker's transaction.
