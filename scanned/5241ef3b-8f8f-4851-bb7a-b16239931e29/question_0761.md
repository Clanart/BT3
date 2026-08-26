# Q0761: DelegateVoteRewardPool.harvestAll - balance held at the instant of the queue captures the whole stream

## Question
In rewards/DelegateVoteRewardPool.sol, because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Starting from a state where totalSupply is zero and queuedRewards holds a backlog, can an unprivileged EOA use `harvestAll()` to leave `userRewards[_rewardToken][account]` inconsistent with `userRewardPerTokenPaid[_rewardToken][account]`, violating the invariant that reward share must be weighted by the time balance was actually held and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: balance held at the instant of the queue captures the whole stream)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Precondition: totalSupply is zero and queuedRewards holds a backlog.
- Invariant to test: reward share must be weighted by the time balance was actually held; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `harvestAll()`: constrain the setup so that totalSupply is zero and queuedRewards holds a backlog, fuzz the attacker inputs (the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone), and assert after every call that reward share must be weighted by the time balance was actually held.
