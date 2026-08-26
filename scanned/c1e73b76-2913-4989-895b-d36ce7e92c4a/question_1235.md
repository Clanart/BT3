# Q1235: BaseRewardPool.donateRewards - donateRewards used to grief the operator's own accounting

## Question
In rewards/BaseRewardPool.sol, donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken)` while rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, and drive `rewardTokens.length` out of agreement with `isRewardToken[_rewardToken]` - breaking the invariant that only an authorised manager may decide when and by how much the global reward index moves - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that only an authorised manager may decide when and by how much the global reward index moves.
