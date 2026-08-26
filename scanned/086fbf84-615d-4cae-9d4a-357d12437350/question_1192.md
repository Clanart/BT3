# Q1192: DelegateVoteRewardPool.harvestAll - no reentrancy guard on harvestAll

## Question
In rewards/DelegateVoteRewardPool.sol, harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Can an unprivileged attacker reach this through `harvestAll()` while the attacker obtains delegate-pool balance in the block before a large bribe lands, and drive `earnedRewards returned by claimAllBribes` out of agreement with `IERC20(rewardToken).balanceOf(address(this))` - breaking the invariant that a function that settles from external claim results must hold a reentrancy guard - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: no reentrancy guard on harvestAll)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Precondition: the attacker obtains delegate-pool balance in the block before a large bribe lands.
- Invariant to test: a function that settles from external claim results must hold a reentrancy guard; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under the attacker obtains delegate-pool balance in the block before a large bribe lands, asserting on every row that a function that settles from external claim results must hold a reentrancy guard.
