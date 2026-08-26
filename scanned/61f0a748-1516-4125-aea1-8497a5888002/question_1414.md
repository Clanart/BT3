# Q1414: DelegateVoteRewardPool.harvestAll - balance held at the instant of the queue captures the whole stream

## Question
In rewards/DelegateVoteRewardPool.sol, because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Can an unprivileged attacker reach this through `harvestAll()` while protocolFee is non-zero and feeCollector is set, and drive `rewards[_rewardToken].rewardPerTokenStored` out of agreement with `totalSupply of the delegate pool` - breaking the invariant that reward share must be weighted by the time balance was actually held - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: balance held at the instant of the queue captures the whole stream)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: reward share must be weighted by the time balance was actually held; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone) under protocolFee is non-zero and feeCollector is set, asserting on every row that reward share must be weighted by the time balance was actually held.
