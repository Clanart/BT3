# Q2025: DelegateVoteRewardPool.harvestAll - no reentrancy guard on harvestAll

## Question
rewards/DelegateVoteRewardPool.sol: harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. With the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone under attacker control and a bribe token has a transfer hook the attacker controls, can an unprivileged caller sequence `harvestAll()` so that `protocolFee` and `earnedRewards[index]` no longer reconcile, violating the invariant that a function that settles from external claim results must hold a reentrancy guard and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: no reentrancy guard on harvestAll)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: a function that settles from external claim results must hold a reentrancy guard; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `harvestAll()`: constrain the setup so that a bribe token has a transfer hook the attacker controls, fuzz the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone), and assert after every call that a function that settles from external claim results must hold a reentrancy guard.
