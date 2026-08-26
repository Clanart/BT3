# Q2013: VLMGP.startUnlock - vote commitment blocks the exit forever

## Question
VLMGP.sol: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Under the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, is there an unprivileged sequence of `startUnlock(uint256 _amountToCoolDown)` that leaves `totalPenalty` unreconciled with `IERC20(MGP).balanceOf(address(this))`, violates the invariant that a user must always retain a path to release their vote commitment and exit their lock, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: vote commitment blocks the exit forever)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Precondition: the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one.
- Invariant to test: a user must always retain a path to release their vote commitment and exit their lock; concretely, `totalPenalty` must stay reconciled with `IERC20(MGP).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds maxSlot slots so getNextAvailableUnlockSlot must reuse a zeroed one, call `startUnlock(uint256 _amountToCoolDown)`, and assert `totalPenalty` equals `IERC20(MGP).balanceOf(address(this))` and that no account can withdraw more than it put in.
