# Q2719: DelegateVoteRewardPool.harvestAll - protocol fee taken from a reported number rather than a real balance

## Question
Consider rewards/DelegateVoteRewardPool.sol, where _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Assuming the victim has a large unsettled userRewards balance in the delegate pool, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].rewardPerTokenStored` and `totalSupply of the delegate pool` via `harvestAll()`, breaking the invariant that a fee must be computed from value the contract actually received and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: protocol fee taken from a reported number rather than a real balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Precondition: the victim has a large unsettled userRewards balance in the delegate pool.
- Invariant to test: a fee must be computed from value the contract actually received; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unsettled userRewards balance in the delegate pool, call `harvestAll()`, and assert `rewards[_rewardToken].rewardPerTokenStored` equals `totalSupply of the delegate pool` and that no account can withdraw more than it put in.
