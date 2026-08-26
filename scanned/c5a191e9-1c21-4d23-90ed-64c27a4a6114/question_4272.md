# Q4272: VLMGP.startUnlock - vote commitment blocks the exit forever

## Question
Consider VLMGP.sol, where startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Assuming the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, can an unprivileged attacker turn this into a divergence between `totalAmount` and `sum of userInfo[vlmgp][*].amount in MasterMagpie` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that a user must always retain a path to release their vote commitment and exit their lock and producing Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: vote commitment blocks the exit forever)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: startUnlock() reverts when getUserTotalLocked(msg.sender) - _amountToCoolDown falls below IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender), and WombatBribeManager.unvote() itself reverts with PoolNotActive for a deactivated pool, so a vote that can no longer be withdrawn locks the MGP behind it. Precondition: the victim has a large unsettled userRewards balance in vlMGPBaseRewarder.
- Invariant to test: a user must always retain a path to release their vote commitment and exit their lock; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled userRewards balance in vlMGPBaseRewarder, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `totalAmount` versus `sum of userInfo[vlmgp][*].amount in MasterMagpie` relation are unchanged by the attacker's transaction.
