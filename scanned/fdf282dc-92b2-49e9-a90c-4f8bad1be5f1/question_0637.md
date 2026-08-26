# Q0637: DelegateVoteRewardPool.getReward - getReward is public and settles any account

## Question
In rewards/DelegateVoteRewardPool.sol, getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Starting from a state where the pool rewarder holds less than the earned figure claimAllBribes reported, can an unprivileged EOA use `getReward(address _for)` to leave `votingWeights[pool] and totalWeight` inconsistent with `the deltas pushed by _updateVote`, violating the invariant that only the account itself may decide when its rewards are settled and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: getReward is public and settles any account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Precondition: the pool rewarder holds less than the earned figure claimAllBribes reported.
- Invariant to test: only the account itself may decide when its rewards are settled; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and the settlement timing) under the pool rewarder holds less than the earned figure claimAllBribes reported, asserting on every row that only the account itself may decide when its rewards are settled.
