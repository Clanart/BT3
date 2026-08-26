# Q3969: VLMGP.startUnlock - vote commitment blocks the exit forever

## Question
In VLMGP.sol, startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Starting from a state where a large vesting MGP distribution has just been queued into the vlMGP rewarder, can an unprivileged EOA use `startUnlock(uint256 _amountToCoolDown)` to leave `getUserAmountInCoolDown(user)` inconsistent with `totalAmountInCoolDown`, violating the invariant that a user must always retain a path to release their vote commitment and exit their lock and extracting Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: vote commitment blocks the exit forever)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Precondition: a large vesting MGP distribution has just been queued into the vlMGP rewarder.
- Invariant to test: a user must always retain a path to release their vote commitment and exit their lock; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `startUnlock(uint256 _amountToCoolDown)`: constrain the setup so that a large vesting MGP distribution has just been queued into the vlMGP rewarder, fuzz the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot), and assert after every call that a user must always retain a path to release their vote commitment and exit their lock.
