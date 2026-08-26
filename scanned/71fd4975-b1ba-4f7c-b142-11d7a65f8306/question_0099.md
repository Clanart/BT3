# Q0099: vlMGPBaseRewarder.updateFor - early-continue skips a real balance change

## Question
rewards/vlMGPBaseRewarder.sol - _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Can an unprivileged attacker controlling the victim address and the block at which their index is pinned, under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, exploit this through `updateFor(address _account)` to break the reconciliation between `forfeitAmount` and `rewardInfo.rewardPerTokenStored` and the invariant that userRewards must capture every balance-weighted segment even when the global index did not move, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, then assert `forfeitAmount` and `rewardInfo.rewardPerTokenStored` end identical in both runs.
