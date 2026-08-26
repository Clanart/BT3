# Q3634: BaseRewardPool.donateRewards - donateRewards used to grief the operator's own accounting

## Question
rewards/BaseRewardPool.sol: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Under a reward-manager queueNewRewards transaction is pending in the mempool, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken)` that leaves `rewards[_rewardToken].queuedRewards` unreconciled with `rewards[_rewardToken].rewardPerTokenStored`, violates the invariant that only an authorised manager may decide when and by how much the global reward index moves, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a reward-manager queueNewRewards transaction is pending in the mempool, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `rewards[_rewardToken].queuedRewards` versus `rewards[_rewardToken].rewardPerTokenStored` relation are unchanged by the attacker's transaction.
