# Q0172: DelegateVoteRewardPool.harvestAll - no reentrancy guard on harvestAll

## Question
rewards/DelegateVoteRewardPool.sol - harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Can an unprivileged attacker controlling the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone, under the bribe contract for a voted pool registers more than one reward token, exploit this through `harvestAll()` to break the reconciliation between `protocolFee` and `earnedRewards[index]` and the invariant that a function that settles from external claim results must hold a reentrancy guard, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: no reentrancy guard on harvestAll)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Precondition: the bribe contract for a voted pool registers more than one reward token.
- Invariant to test: a function that settles from external claim results must hold a reentrancy guard; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the bribe contract for a voted pool registers more than one reward token, have the attacker run `harvestAll()`, then assert the victim's claimable value and the `protocolFee` versus `earnedRewards[index]` relation are unchanged by the attacker's transaction.
