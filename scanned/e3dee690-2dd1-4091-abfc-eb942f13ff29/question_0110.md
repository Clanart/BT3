# Q0110: DelegateVoteRewardPool.harvestAll - protocol fee taken from a reported number rather than a real balance

## Question
rewards/DelegateVoteRewardPool.sol: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. With the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone under attacker control and the bribe contract for a voted pool registers more than one reward token, can an unprivileged caller sequence `harvestAll()` so that `_balances[account]` and `totalSupply` no longer reconcile, violating the invariant that a fee must be computed from value the contract actually received and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: protocol fee taken from a reported number rather than a real balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Precondition: the bribe contract for a voted pool registers more than one reward token.
- Invariant to test: a fee must be computed from value the contract actually received; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bribe contract for a voted pool registers more than one reward token, call `harvestAll()`, and assert `_balances[account]` equals `totalSupply` and that no account can withdraw more than it put in.
