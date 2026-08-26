# Q0854: DelegateVoteRewardPool.harvestAll - no reentrancy guard on harvestAll

## Question
Note that in rewards/DelegateVoteRewardPool.sol, harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under totalSupply is zero and queuedRewards holds a backlog and force `userRewards[_rewardToken][account]` apart from `userRewardPerTokenPaid[_rewardToken][account]`, breaking the invariant that a function that settles from external claim results must hold a reentrancy guard for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: no reentrancy guard on harvestAll)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Precondition: totalSupply is zero and queuedRewards holds a backlog.
- Invariant to test: a function that settles from external claim results must hold a reentrancy guard; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under totalSupply is zero and queuedRewards holds a backlog, then assert `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]` end identical in both runs.
