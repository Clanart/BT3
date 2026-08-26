# Q2232: DelegateVoteRewardPool.harvestAll - protocol fee taken from a reported number rather than a real balance

## Question
In rewards/DelegateVoteRewardPool.sol, _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Starting from a state where the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, can an unprivileged EOA use `harvestAll()` to leave `userRewards[_rewardToken][account]` inconsistent with `userRewardPerTokenPaid[_rewardToken][account]`, violating the invariant that a fee must be computed from value the contract actually received and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: protocol fee taken from a reported number rather than a real balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Precondition: the delegated pool holds a dominant share of one pool's totalVoteInVlmgp.
- Invariant to test: a fee must be computed from value the contract actually received; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `harvestAll()`: constrain the setup so that the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, fuzz the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone), and assert after every call that a fee must be computed from value the contract actually received.
