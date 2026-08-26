# Q2841: BaseRewardPool.donateRewards - donateRewards used to grief the operator's own accounting

## Question
Note that in rewards/BaseRewardPool.sol, donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small and force `totalStaked()` apart from `IERC20(stakingToken).balanceOf(operator)`, breaking the invariant that only an authorised manager may decide when and by how much the global reward index moves for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, call `donateRewards(uint256 _amountReward, address _rewardToken)`, and assert `totalStaked()` equals `IERC20(stakingToken).balanceOf(operator)` and that no account can withdraw more than it put in.
