# Q2531: DelegateVoteRewardPool.harvestAll - no reentrancy guard on harvestAll

## Question
In rewards/DelegateVoteRewardPool.sol, harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Starting from a state where a keeper castVotes transaction that ends in harvestAll is pending in the mempool, can an unprivileged EOA use `harvestAll()` to leave `userRewards[_rewardToken][account]` inconsistent with `userRewardPerTokenPaid[_rewardToken][account]`, violating the invariant that a function that settles from external claim results must hold a reentrancy guard and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: no reentrancy guard on harvestAll)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Precondition: a keeper castVotes transaction that ends in harvestAll is pending in the mempool.
- Invariant to test: a function that settles from external claim results must hold a reentrancy guard; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under a keeper castVotes transaction that ends in harvestAll is pending in the mempool, asserting on every row that a function that settles from external claim results must hold a reentrancy guard.
