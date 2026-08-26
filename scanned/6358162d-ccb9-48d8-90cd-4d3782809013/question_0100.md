# Q0100: mWOMSVBaseRewarder.updateFor - early-continue skips a real balance change

## Question
In rewards/mWOMSVBaseRewarder.sol, _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Does `updateFor(address _account)` let an unprivileged caller exploit that under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, so that `forfeitAmount` diverges from `rewardInfo.rewardPerTokenStored`, the invariant that userRewards must capture every balance-weighted segment even when the global index did not move is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, asserting at the end that `forfeitAmount` still equals `rewardInfo.rewardPerTokenStored` and the PoC's balance delta is non-positive.
