# Q4351: vlMGPBaseRewarder.updateFor - early-continue skips a real balance change

## Question
In rewards/vlMGPBaseRewarder.sol, _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Can an unprivileged attacker reach this through `updateFor(address _account)` while the victim has not settled for several epochs and holds a large userRewards balance, and drive `_calExpireForfeit(account,_amount)` out of agreement with `vlMGP.getRewardablePercentWAD(account)` - breaking the invariant that userRewards must capture every balance-weighted segment even when the global index did not move - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the victim has not settled for several epochs and holds a large userRewards balance, snapshot `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
