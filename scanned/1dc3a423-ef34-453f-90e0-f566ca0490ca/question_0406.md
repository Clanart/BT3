# Q0406: BaseRewardPoolV2.donateRewards - donateRewards used to grief the operator's own accounting

## Question
In rewards/BaseRewardPoolV2.sol, donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Starting from a state where the pool has exactly one registered reward token and no queued backlog, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `10**stakingDecimals()` inconsistent with `totalStaked()`, violating the invariant that only an authorised manager may decide when and by how much the global reward index moves and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the pool has exactly one registered reward token and no queued backlog, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `10**stakingDecimals()` versus `totalStaked()` relation are unchanged by the attacker's transaction.
