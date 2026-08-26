# Q2186: DelegateVoteRewardPool.harvestAll - harvestAll is permissionless and sets the distribution instant

## Question
In rewards/DelegateVoteRewardPool.sol, harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Starting from a state where the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, can an unprivileged EOA use `harvestAll()` to leave `protocolFee` inconsistent with `earnedRewards[index]`, violating the invariant that the instant at which a shared reward stream is distributed must not be selectable by an unrelated party and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: harvestAll is permissionless and sets the distribution instant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() has no caller restriction, so any address decides the block at which the entire delegate bribe stream is claimed, fee-charged and folded into rewardPerTokenStored. Precondition: the delegated pool holds a dominant share of one pool's totalVoteInVlmgp.
- Invariant to test: the instant at which a shared reward stream is distributed must not be selectable by an unrelated party; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, then assert `protocolFee` and `earnedRewards[index]` end identical in both runs.
