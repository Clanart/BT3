# Q2347: DelegateVoteRewardPool.getReward - _getDelegateReward zeroes the entitlement before the transfer

## Question
In rewards/DelegateVoteRewardPool.sol, _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Can an unprivileged attacker reach this through `getReward(address _for)` while the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, and drive `rewards[_rewardToken].rewardPerTokenStored` out of agreement with `totalSupply of the delegate pool` - breaking the invariant that an entitlement may only be cleared once the exact amount has been delivered - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: _getDelegateReward zeroes the entitlement before the transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Precondition: the delegated pool holds a dominant share of one pool's totalVoteInVlmgp.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and the settlement timing) under the delegated pool holds a dominant share of one pool's totalVoteInVlmgp, asserting on every row that an entitlement may only be cleared once the exact amount has been delivered.
