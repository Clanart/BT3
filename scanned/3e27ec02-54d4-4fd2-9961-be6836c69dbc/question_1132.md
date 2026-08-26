# Q1132: DelegateVoteRewardPool.harvestAll - protocol fee taken from a reported number rather than a real balance

## Question
rewards/DelegateVoteRewardPool.sol: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Under the attacker obtains delegate-pool balance in the block before a large bribe lands, is there an unprivileged sequence of `harvestAll()` that leaves `rewards[_rewardToken].rewardPerTokenStored` unreconciled with `totalSupply of the delegate pool`, violates the invariant that a fee must be computed from value the contract actually received, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: protocol fee taken from a reported number rather than a real balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Precondition: the attacker obtains delegate-pool balance in the block before a large bribe lands.
- Invariant to test: a fee must be computed from value the contract actually received; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `harvestAll()` sequence atomically under the attacker obtains delegate-pool balance in the block before a large bribe lands, asserting at the end that `rewards[_rewardToken].rewardPerTokenStored` still equals `totalSupply of the delegate pool` and the PoC's balance delta is non-positive.
