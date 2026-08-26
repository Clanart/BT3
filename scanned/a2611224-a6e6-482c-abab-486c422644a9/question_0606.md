# Q0606: DelegateVoteRewardPool.getReward - _getDelegateReward zeroes the entitlement before the transfer

## Question
In rewards/DelegateVoteRewardPool.sol, _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Starting from a state where the pool rewarder holds less than the earned figure claimAllBribes reported, can an unprivileged EOA use `getReward(address _for)` to leave `rewards[_rewardToken].rewardPerTokenStored` inconsistent with `totalSupply of the delegate pool`, violating the invariant that an entitlement may only be cleared once the exact amount has been delivered and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: _getDelegateReward zeroes the entitlement before the transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Precondition: the pool rewarder holds less than the earned figure claimAllBribes reported.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _for)`: constrain the setup so that the pool rewarder holds less than the earned figure claimAllBribes reported, fuzz the attacker inputs (_for (any victim) and the settlement timing), and assert after every call that an entitlement may only be cleared once the exact amount has been delivered.
