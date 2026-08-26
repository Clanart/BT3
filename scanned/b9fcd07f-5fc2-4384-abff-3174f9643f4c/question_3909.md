# Q3909: mWomSV.startUnlock - ArbWomUp3 tier reads the same locked balance the deposit just changed

## Question
In wombat/mWomSV.sol, ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Can an unprivileged attacker reach this through `startUnlock(uint256 _amountToCoolDown)` while the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, and drive `getRewardablePercentWAD(user)` out of agreement with `_calExpireForfeit in mWOMSVBaseRewarder` - breaking the invariant that a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: ArbWomUp3 tier reads the same locked balance the deposit just changed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, snapshot `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder`, run the attacker's `startUnlock(uint256 _amountToCoolDown)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
