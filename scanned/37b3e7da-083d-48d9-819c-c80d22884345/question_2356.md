# Q2356: BaseRewardPool.donateRewards - donateRewards used to grief the operator's own accounting

## Question
rewards/BaseRewardPool.sol: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. With _amountReward down to one wei and which registered reward token is provisioned under attacker control and the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, can an unprivileged caller sequence `donateRewards(uint256 _amountReward, address _rewardToken)` so that `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` no longer reconcile, violating the invariant that only an authorised manager may decide when and by how much the global reward index moves and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `userRewards[_rewardToken][account]` versus `earned(account,_rewardToken)` relation are unchanged by the attacker's transaction.
