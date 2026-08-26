# Q2048: DelegateVoteRewardPool.harvestAll - queued backlog released to whoever holds balance at the flush

## Question
rewards/DelegateVoteRewardPool.sol: _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. With the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone under attacker control and a bribe token has a transfer hook the attacker controls, can an unprivileged caller sequence `harvestAll()` so that `_balances[account]` and `totalSupply` no longer reconcile, violating the invariant that a backlog accrued while the pool was empty must not be assignable to one holder and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queued backlog released to whoever holds balance at the flush)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to one holder; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish a bribe token has a transfer hook the attacker controls, have the attacker run `harvestAll()`, then assert the victim's claimable value and the `_balances[account]` versus `totalSupply` relation are unchanged by the attacker's transaction.
