# Q3606: vlMGPBaseRewarder.updateFor - early-continue skips a real balance change

## Question
rewards/vlMGPBaseRewarder.sol: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Under totalStaked is zero and queuedRewards holds a backlog, is there an unprivileged sequence of `updateFor(address _account)` that leaves `forfeitAmount` unreconciled with `rewardInfo.rewardPerTokenStored`, violates the invariant that userRewards must capture every balance-weighted segment even when the global index did not move, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that totalStaked is zero and queuedRewards holds a backlog, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that userRewards must capture every balance-weighted segment even when the global index did not move.
