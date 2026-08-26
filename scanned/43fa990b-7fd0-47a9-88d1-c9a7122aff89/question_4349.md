# Q4349: BaseRewardPoolV2.donateRewards - donateRewards used to grief the operator's own accounting

## Question
Consider rewards/BaseRewardPoolV2.sol, where donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Assuming a previously registered reward token has begun reverting on transfer, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that only an authorised manager may decide when and by how much the global reward index moves and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a previously registered reward token has begun reverting on transfer, then assert `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` end identical in both runs.
