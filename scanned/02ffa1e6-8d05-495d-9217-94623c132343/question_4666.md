# Q4666: vlMGPBaseRewarder.updateFor - early-continue skips a real balance change

## Question
In rewards/vlMGPBaseRewarder.sol, _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Starting from a state where a registered reward token has begun reverting on transfer, can an unprivileged EOA use `updateFor(address _account)` to leave `userRewards[_rewardToken][account]` inconsistent with `rewards[_rewardToken].rewardPerTokenStored`, violating the invariant that userRewards must capture every balance-weighted segment even when the global index did not move and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a registered reward token has begun reverting on transfer, call `updateFor(address _account)`, and assert `userRewards[_rewardToken][account]` equals `rewards[_rewardToken].rewardPerTokenStored` and that no account can withdraw more than it put in.
