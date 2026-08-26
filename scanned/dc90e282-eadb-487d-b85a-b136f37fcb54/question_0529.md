# Q0529: BaseRewardPool.donateRewards - donateRewards used to grief the operator's own accounting

## Question
Note that in rewards/BaseRewardPool.sol, donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken)` under the pool has exactly one registered reward token and no queued backlog and force `10**stakingDecimals()` apart from `totalStaked()`, breaking the invariant that only an authorised manager may decide when and by how much the global reward index moves for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that the pool has exactly one registered reward token and no queued backlog, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that only an authorised manager may decide when and by how much the global reward index moves.
