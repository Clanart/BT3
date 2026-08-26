# Q1588: mWOMSVBaseRewarder.updateFor - early-continue skips a real balance change

## Question
In rewards/mWOMSVBaseRewarder.sol, _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Does `updateFor(address _account)` let an unprivileged caller exploit that under the computed forfeit lands just below the _amount / 1000 dust threshold, so that `_calExpireForfeit(account,_amount)` diverges from `mWOMSV.getRewardablePercentWAD(account)`, the invariant that userRewards must capture every balance-weighted segment even when the global index did not move is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the computed forfeit lands just below the _amount / 1000 dust threshold, then assert `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` end identical in both runs.
