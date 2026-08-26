# Q4667: mWOMSVBaseRewarder.updateFor - early-continue skips a real balance change

## Question
rewards/mWOMSVBaseRewarder.sol - _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Can an unprivileged attacker controlling the victim address and the block at which their index is pinned, under a registered reward token has begun reverting on transfer, exploit this through `updateFor(address _account)` to break the reconciliation between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` and the invariant that userRewards must capture every balance-weighted segment even when the global index did not move, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that a registered reward token has begun reverting on transfer, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that userRewards must capture every balance-weighted segment even when the global index did not move.
