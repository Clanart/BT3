# Q1989: BaseRewardPoolV2.donateRewards - donateRewards used to grief the operator's own accounting

## Question
Consider rewards/BaseRewardPoolV2.sol, where donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Assuming the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that only an authorised manager may decide when and by how much the global reward index moves and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that only an authorised manager may decide when and by how much the global reward index moves.
