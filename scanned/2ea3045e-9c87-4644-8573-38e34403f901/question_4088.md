# Q4088: BaseRewardPoolV2.donateRewards - donateRewards used to grief the operator's own accounting

## Question
In rewards/BaseRewardPoolV2.sol, donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Does `donateRewards(uint256 _amountReward, address _rewardToken)` let an unprivileged caller exploit that under the reward token charges a transfer fee so the received balance is below the requested amount, so that `rewardTokens.length` diverges from `isRewardToken[_rewardToken]`, the invariant that only an authorised manager may decide when and by how much the global reward index moves is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: the reward token charges a transfer fee so the received balance is below the requested amount.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the reward token charges a transfer fee so the received balance is below the requested amount, snapshot `rewardTokens.length` and `isRewardToken[_rewardToken]`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
