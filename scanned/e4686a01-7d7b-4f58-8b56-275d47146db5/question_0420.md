# Q0420: DelegateVoteRewardPool.harvestAll - balance held at the instant of the queue captures the whole stream

## Question
Note that in rewards/DelegateVoteRewardPool.sol, because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under the pool rewarder holds less than the earned figure claimAllBribes reported and force `_balances[account]` apart from `totalSupply`, breaking the invariant that reward share must be weighted by the time balance was actually held for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: balance held at the instant of the queue captures the whole stream)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Precondition: the pool rewarder holds less than the earned figure claimAllBribes reported.
- Invariant to test: reward share must be weighted by the time balance was actually held; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool rewarder holds less than the earned figure claimAllBribes reported, then assert `_balances[account]` and `totalSupply` end identical in both runs.
