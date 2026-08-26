# Q4545: VLMGP.startUnlock - vote commitment blocks the exit forever

## Question
In VLMGP.sol, startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Starting from a state where the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, can an unprivileged EOA use `startUnlock(uint256 _amountToCoolDown)` to leave `getRewardablePercentWAD(user)` inconsistent with `userUnlockings[user][i].amountInCoolDown`, violating the invariant that a user must always retain a path to release their vote commitment and exit their lock and extracting Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: vote commitment blocks the exit forever)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Precondition: the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit.
- Invariant to test: a user must always retain a path to release their vote commitment and exit their lock; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot) under the attacker splits one exit across several slots to compare aggregate penalty against a single-slot exit, asserting on every row that a user must always retain a path to release their vote commitment and exit their lock.
