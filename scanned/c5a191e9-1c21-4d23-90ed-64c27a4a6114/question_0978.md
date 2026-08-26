# Q0978: DelegateVoteRewardPool.getReward - getReward is public and settles any account

## Question
In rewards/DelegateVoteRewardPool.sol, getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Can an unprivileged attacker reach this through `getReward(address _for)` while totalSupply is zero and queuedRewards holds a backlog, and drive `protocolFee` out of agreement with `earnedRewards[index]` - breaking the invariant that only the account itself may decide when its rewards are settled - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: getReward is public and settles any account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Precondition: totalSupply is zero and queuedRewards holds a backlog.
- Invariant to test: only the account itself may decide when its rewards are settled; concretely, `protocolFee` must stay reconciled with `earnedRewards[index]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish totalSupply is zero and queuedRewards holds a backlog, have the attacker run `getReward(address _for)`, then assert the victim's claimable value and the `protocolFee` versus `earnedRewards[index]` relation are unchanged by the attacker's transaction.
