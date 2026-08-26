# Q3421: BaseRewardPoolV2.donateRewards - first-staker capture of the queued backlog

## Question
rewards/BaseRewardPoolV2.sol: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. With _amountReward down to one wei and which registered reward token is provisioned under attacker control and the attacker funds the action with a flash loan of the staking token repaid in the same transaction, can an unprivileged caller sequence `donateRewards(uint256 _amountReward, address _rewardToken)` so that `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` no longer reconcile, violating the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker funds the action with a flash loan of the staking token repaid in the same transaction, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `rewards[_rewardToken].queuedRewards` equals `rewards[_rewardToken].rewardPerTokenStored` and that no account can withdraw more than it put in.
