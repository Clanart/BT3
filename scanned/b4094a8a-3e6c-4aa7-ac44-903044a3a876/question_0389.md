# Q0389: DelegateVoteRewardPool.harvestAll - harvestAll is permissionless and sets the distribution instant

## Question
Note that in rewards/DelegateVoteRewardPool.sol, harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under the pool rewarder holds less than the earned figure claimAllBribes reported and force `protocolFee` apart from `earnedRewards[index]`, breaking the invariant that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: harvestAll is permissionless and sets the distribution instant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Precondition: the pool rewarder holds less than the earned figure claimAllBribes reported.
- Invariant to test: the instant at which a shared reward stream is distributed must not be selectable by an unrelated party; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under the pool rewarder holds less than the earned figure claimAllBribes reported, asserting on every row that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party.
