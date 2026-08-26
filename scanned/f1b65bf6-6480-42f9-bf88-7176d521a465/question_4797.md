# Q4797: VLMGP.startUnlock - vote commitment blocks the exit forever

## Question
VLMGP.sol: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Under the attacker repeats cancelUnlock and startUnlock inside a single transaction, is there an unprivileged sequence of `startUnlock(uint256 _amountToCoolDown)` that leaves `userUnlockings[user][i].endTime` unreconciled with `block.timestamp`, violates the invariant that a user must always retain a path to release their vote commitment and exit their lock, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: vote commitment blocks the exit forever)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Precondition: the attacker repeats cancelUnlock and startUnlock inside a single transaction.
- Invariant to test: a user must always retain a path to release their vote commitment and exit their lock; concretely, `userUnlockings[user][i].endTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker repeats cancelUnlock and startUnlock inside a single transaction, snapshot `userUnlockings[user][i].endTime` and `block.timestamp`, run the attacker's `startUnlock(uint256 _amountToCoolDown)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
