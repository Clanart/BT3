# Q0872: VLMGP.startUnlock - getUserTotalLocked underflows and bricks every read

## Question
VLMGP.sol: getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. With _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot under attacker control and the attacker's slot matured exactly one second ago, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `totalAmount` and `sum of userInfo[vlmgp][*].amount in MasterMagpie` no longer reconcile, violating the invariant that the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position and realising Critical - Permanent freezing of funds?

## Target
- File/function: VLMGP.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflows and bricks every read)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamp that fixes startTime/endTime for the slot
- Exploit idea: getUserTotalLocked() computes stakingInfo(...).staked - getUserAmountInCoolDown(user) with no floor, so any state in which the MasterMagpie stake falls below the sum of the user's cooldown slots makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder claim revert permanently for that user. Precondition: the attacker's slot matured exactly one second ago.
- Invariant to test: the locked-balance accessor must never be able to revert; a user must always be able to read and exit their position; concretely, `totalAmount` must stay reconciled with `sum of userInfo[vlmgp][*].amount in MasterMagpie`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker's slot matured exactly one second ago, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `totalAmount` versus `sum of userInfo[vlmgp][*].amount in MasterMagpie` relation are unchanged by the attacker's transaction.
