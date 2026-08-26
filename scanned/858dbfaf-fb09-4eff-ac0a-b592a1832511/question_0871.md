# Q0871: BaseRewardPoolV2.donateRewards - first-staker capture of the queued backlog

## Question
In rewards/BaseRewardPoolV2.sol, while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Starting from a state where rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `10**stakingDecimals()` inconsistent with `totalStaked()`, violating the invariant that a backlog accrued while the pool was empty must not be assignable to a single one-block depositor and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: first-staker capture of the queued backlog)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: while totalStaked() == 0 every provision accumulates into queuedRewards, and the first address to obtain stake before the next donateRewards call absorbs the entire backlog through a single rewardPerTokenStored jump. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to a single one-block depositor; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, snapshot `10**stakingDecimals()` and `totalStaked()`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
