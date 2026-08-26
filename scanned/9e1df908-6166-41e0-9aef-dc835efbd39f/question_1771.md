# Q1771: DelegateVoteRewardPool.harvestAll - no reentrancy guard on harvestAll

## Question
In rewards/DelegateVoteRewardPool.sol, harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Starting from a state where protocolFee is zero so the whole reported amount is queued, can an unprivileged EOA use `harvestAll()` to leave `votingWeights[pool] and totalWeight` inconsistent with `the deltas pushed by _updateVote`, violating the invariant that a function that settles from external claim results must hold a reentrancy guard and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: no reentrancy guard on harvestAll)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: a function that settles from external claim results must hold a reentrancy guard; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under protocolFee is zero so the whole reported amount is queued, asserting on every row that a function that settles from external claim results must hold a reentrancy guard.
