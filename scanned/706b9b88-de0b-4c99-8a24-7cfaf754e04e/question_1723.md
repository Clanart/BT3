# Q1723: DelegateVoteRewardPool.harvestAll - protocol fee taken from a reported number rather than a real balance

## Question
Note that in rewards/DelegateVoteRewardPool.sol, _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under protocolFee is zero so the whole reported amount is queued and force `protocolFee` apart from `earnedRewards[index]`, breaking the invariant that a fee must be computed from value the contract actually received for Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: protocol fee taken from a reported number rather than a real balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: a fee must be computed from value the contract actually received; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `harvestAll()`: constrain the setup so that protocolFee is zero so the whole reported amount is queued, fuzz the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone), and assert after every call that a fee must be computed from value the contract actually received.
