# Q1673: DelegateVoteRewardPool.harvestAll - harvestAll is permissionless and sets the distribution instant

## Question
Consider rewards/DelegateVoteRewardPool.sol, where harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Assuming protocolFee is zero so the whole reported amount is queued, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].rewardPerTokenStored` and `totalSupply of the delegate pool` via `harvestAll()`, breaking the invariant that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: harvestAll is permissionless and sets the distribution instant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: the instant at which a shared reward stream is distributed must not be selectable by an unrelated party; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under protocolFee is zero so the whole reported amount is queued, asserting on every row that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party.
