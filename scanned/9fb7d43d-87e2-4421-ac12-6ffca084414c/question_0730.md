# Q0730: DelegateVoteRewardPool.harvestAll - harvestAll is permissionless and sets the distribution instant

## Question
In rewards/DelegateVoteRewardPool.sol, harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Starting from a state where totalSupply is zero and queuedRewards holds a backlog, can an unprivileged EOA use `harvestAll()` to leave `_balances[account]` inconsistent with `totalSupply`, violating the invariant that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: harvestAll is permissionless and sets the distribution instant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Precondition: totalSupply is zero and queuedRewards holds a backlog.
- Invariant to test: the instant at which a shared reward stream is distributed must not be selectable by an unrelated party; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish totalSupply is zero and queuedRewards holds a backlog, have the attacker run `harvestAll()`, then assert the victim's claimable value and the `_balances[account]` versus `totalSupply` relation are unchanged by the attacker's transaction.
