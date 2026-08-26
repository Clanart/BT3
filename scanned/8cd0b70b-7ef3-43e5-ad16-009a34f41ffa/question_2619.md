# Q2619: DelegateVoteRewardPool.getReward - getReward is public and settles any account

## Question
rewards/DelegateVoteRewardPool.sol: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. With _for (any victim) and the settlement timing under attacker control and a keeper castVotes transaction that ends in harvestAll is pending in the mempool, can an unprivileged caller sequence `getReward(address _for)` so that `protocolFee` and `earnedRewards[index]` no longer reconcile, violating the invariant that only the account itself may decide when its rewards are settled and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: getReward is public and settles any account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Precondition: a keeper castVotes transaction that ends in harvestAll is pending in the mempool.
- Invariant to test: only the account itself may decide when its rewards are settled; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up a keeper castVotes transaction that ends in harvestAll is pending in the mempool, snapshot `protocolFee` and `earnedRewards[index]`, run the attacker's `getReward(address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
