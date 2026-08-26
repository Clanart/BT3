# Q0467: BaseRewardPool.donateRewards - early-continue skips a genuine balance change

## Question
Note that in rewards/BaseRewardPool.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under the pool has exactly one registered reward token and no queued backlog and force `rewards[_rewardToken].queuedRewards` apart from `rewards[_rewardToken].rewardPerTokenStored`, breaking the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool has exactly one registered reward token and no queued backlog, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `rewards[_rewardToken].queuedRewards` equals `rewards[_rewardToken].rewardPerTokenStored` and that no account can withdraw more than it put in.
