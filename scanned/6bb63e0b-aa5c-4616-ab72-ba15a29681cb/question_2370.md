# Q2370: DelegateVoteRewardPool.getReward - getReward is public and settles any account

## Question
In rewards/DelegateVoteRewardPool.sol, getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Can an unprivileged attacker reach this through `getReward(address _for)` while the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, and drive `votingWeights[pool] and totalWeight` out of agreement with `the deltas pushed by _updateVote` - breaking the invariant that only the account itself may decide when its rewards are settled - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: getReward is public and settles any account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Precondition: the delegated pool holds a dominant share of one pool's totalVoteInVlmgp.
- Invariant to test: only the account itself may decide when its rewards are settled; concretely, `votingWeights[pool] and totalWeight` must stay reconciled with `the deltas pushed by _updateVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _for)`: constrain the setup so that the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, fuzz the attacker inputs (_for (any victim) and the settlement timing), and assert after every call that only the account itself may decide when its rewards are settled.
