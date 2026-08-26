# Q0903: VLMGP.startUnlock - vote commitment blocks the exit forever

## Question
VLMGP.sol: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. With _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot under attacker control and the attacker's slot matured exactly one second ago, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `getRewardablePercentWAD(user)` and `userUnlockings[user][i].amountInCoolDown` no longer reconcile, violating the invariant that a user must always retain a path to release their vote commitment and exit their lock and realising Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: vote commitment blocks the exit forever)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: a user must always retain a path to release their vote commitment and exit their lock; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `userUnlockings[user][i].amountInCoolDown`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `startUnlock(uint256 _amountToCoolDown)`: constrain the setup so that the attacker's slot matured exactly one second ago, fuzz the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot), and assert after every call that a user must always retain a path to release their vote commitment and exit their lock.
