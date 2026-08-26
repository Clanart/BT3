# Q1956: DelegateVoteRewardPool.harvestAll - balance held at the instant of the queue captures the whole stream

## Question
Note that in rewards/DelegateVoteRewardPool.sol, because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under a bribe token has a transfer hook the attacker controls and force `protocolFee` apart from `earnedRewards[index]`, breaking the invariant that reward share must be weighted by the time balance was actually held for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: balance held at the instant of the queue captures the whole stream)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Precondition: a bribe token has a transfer hook the attacker controls.
- Invariant to test: reward share must be weighted by the time balance was actually held; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `harvestAll()` sequence atomically under a bribe token has a transfer hook the attacker controls, asserting at the end that `protocolFee` still equals `earnedRewards[index]` and the PoC's balance delta is non-positive.
