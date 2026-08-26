# Q4887: BaseRewardPool.donateRewards - donateRewards used to grief the operator's own accounting

## Question
rewards/BaseRewardPool.sol - donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under a previously registered reward token has begun reverting on transfer, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` and the invariant that only an authorised manager may decide when and by how much the global reward index moves, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: a previously registered reward token has begun reverting on transfer.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that a previously registered reward token has begun reverting on transfer, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that only an authorised manager may decide when and by how much the global reward index moves.
