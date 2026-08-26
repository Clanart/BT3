# Q1933: DelegateVoteRewardPool.harvestAll - harvestAll is permissionless and sets the distribution instant

## Question
Note that in rewards/DelegateVoteRewardPool.sol, harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under a bribe token has a transfer hook the attacker controls and force `votingWeights[pool] and totalWeight` apart from `the deltas pushed by _updateVote`, breaking the invariant that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: harvestAll is permissionless and sets the distribution instant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: the instant at which a shared reward stream is distributed must not be selectable by an unrelated party; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a bribe token has a transfer hook the attacker controls, call `harvestAll()`, and assert `votingWeights[pool] and totalWeight` equals `the deltas pushed by _updateVote` and that no account can withdraw more than it put in.
