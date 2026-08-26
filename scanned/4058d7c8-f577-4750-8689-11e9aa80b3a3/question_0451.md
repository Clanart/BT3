# Q0451: DelegateVoteRewardPool.harvestAll - protocol fee taken from a reported number rather than a real balance

## Question
Note that in rewards/DelegateVoteRewardPool.sol, _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under the pool rewarder holds less than the earned figure claimAllBribes reported and force `userRewards[_rewardToken][account]` apart from `userRewardPerTokenPaid[_rewardToken][account]`, breaking the invariant that a fee must be computed from value the contract actually received for Critical - Protocol insolvency?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: protocol fee taken from a reported number rather than a real balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _manageRewards() computes fees = protocolFee * earnedRewards[index] / DENOMINATOR and transfers that out of the contract's balance before queueing the remainder, so an over-reported earnedRewards moves real tokens out against a phantom figure. Precondition: the pool rewarder holds less than the earned figure claimAllBribes reported.
- Invariant to test: a fee must be computed from value the contract actually received; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the pool rewarder holds less than the earned figure claimAllBribes reported, snapshot `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]`, run the attacker's `harvestAll()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
