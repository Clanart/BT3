# Q1979: DelegateVoteRewardPool.harvestAll - protocol fee taken from a reported number rather than a real balance

## Question
Note that in rewards/DelegateVoteRewardPool.sol, _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under a bribe token has a transfer hook the attacker controls and force `_balances[account]` apart from `totalSupply`, breaking the invariant that a fee must be computed from value the contract actually received for Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: protocol fee taken from a reported number rather than a real balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: a fee must be computed from value the contract actually received; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up a bribe token has a transfer hook the attacker controls, snapshot `_balances[account]` and `totalSupply`, run the attacker's `harvestAll()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
