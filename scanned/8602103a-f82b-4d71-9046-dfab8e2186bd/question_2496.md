# Q2496: VLMGP.startUnlock - vote commitment blocks the exit forever

## Question
Consider VLMGP.sol, where startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Assuming the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, can an unprivileged attacker turn this into a divergence between `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that a user must always retain a path to release their vote commitment and exit their lock and producing Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: vote commitment blocks the exit forever)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: a user must always retain a path to release their vote commitment and exit their lock; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `startUnlock(uint256 _amountToCoolDown)`: constrain the setup so that the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, fuzz the attacker inputs (_amountToCoolDown and the timestamp that fixes startTime/endTime for the slot), and assert after every call that a user must always retain a path to release their vote commitment and exit their lock.
