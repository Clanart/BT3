# Q2278: DelegateVoteRewardPool.harvestAll - no reentrancy guard on harvestAll

## Question
Note that in rewards/DelegateVoteRewardPool.sol, harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under the delegated pool holds a dominant share of one pool's totalVoteInVlmgp and force `_balances[account]` apart from `totalSupply`, breaking the invariant that a function that settles from external claim results must hold a reentrancy guard for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: no reentrancy guard on harvestAll)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Precondition: the delegated pool holds a dominant share of one pool's totalVoteInVlmgp.
- Invariant to test: a function that settles from external claim results must hold a reentrancy guard; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `harvestAll()` sequence atomically under the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, asserting at the end that `_balances[account]` still equals `totalSupply` and the PoC's balance delta is non-positive.
