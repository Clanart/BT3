# Q3906: BaseRewardPool.donateRewards - first-staker capture of the queued backlog

## Question
Note that in rewards/BaseRewardPool.sol, while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under the attacker funds the action with a flash loan of the staking token repaid in the same transaction and force `rewards[_rewardToken].queuedRewards` apart from `rewards[_rewardToken].rewardPerTokenStored`, breaking the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, then assert `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` end identical in both runs.
