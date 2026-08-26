# Q4322: BaseRewardPool.donateRewards - donateRewards used to grief the operator's own accounting

## Question
rewards/BaseRewardPool.sol: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Under the victim has not been settled for several epochs and holds a large userRewards balance, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken)` that leaves `10**stakingDecimals()` unreconciled with `totalStaked()`, violates the invariant that only an authorised manager may decide when and by how much the global reward index moves, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that the victim has not been settled for several epochs and holds a large userRewards balance, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that only an authorised manager may decide when and by how much the global reward index moves.
