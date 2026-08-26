# Q1698: DelegateVoteRewardPool.harvestAll - balance held at the instant of the queue captures the whole stream

## Question
rewards/DelegateVoteRewardPool.sol - because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Can an unprivileged attacker controlling the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone, under protocolFee is zero so the whole reported amount is queued, exploit this through `harvestAll()` to break the reconciliation between `votingWeights[pool] and totalWeight` and `the deltas pushed by _updateVote` and the invariant that reward share must be weighted by the time balance was actually held, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: balance held at the instant of the queue captures the whole stream)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: because _queueNewRewardsWithoutTransfer raises rewardPerTokenStored against the live totalSupply and harvestAll is permissionless, an attacker who obtains delegate-pool balance immediately before calling captures a share of a full epoch. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: reward share must be weighted by the time balance was actually held; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `harvestAll()` sequence atomically under protocolFee is zero so the whole reported amount is queued, asserting at the end that `votingWeights[pool] and totalWeight` still equals `the deltas pushed by _updateVote` and the PoC's balance delta is non-positive.
