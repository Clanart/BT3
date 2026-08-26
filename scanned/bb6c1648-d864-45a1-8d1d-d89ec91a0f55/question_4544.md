# Q4544: BaseRewardPoolV2.donateRewards - first-staker capture of the queued backlog

## Question
rewards/BaseRewardPoolV2.sol - while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under the attacker calls the function twice in the same block to observe the second, early-continued iteration, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` and the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the attacker calls the function twice in the same block to observe the second, early-continued iteration.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls the function twice in the same block to observe the second, early-continued iteration, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `rewards[_rewardToken].rewardPerTokenStored` equals `userRewardPerTokenPaid[_rewardToken][account]` and that no account can withdraw more than it put in.
