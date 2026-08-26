# Q1841: DelegateVoteRewardPool.getReward - _getDelegateReward zeroes the entitlement before the transfer

## Question
Note that in rewards/DelegateVoteRewardPool.sol, _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Can an attacker holding only tokens bought on market reach it via `getReward(address _for)` under protocolFee is zero so the whole reported amount is queued and force `userRewards[_rewardToken][account]` apart from `userRewardPerTokenPaid[_rewardToken][account]`, breaking the invariant that an entitlement may only be cleared once the exact amount has been delivered for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: _getDelegateReward zeroes the entitlement before the transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _for)`: constrain the setup so that protocolFee is zero so the whole reported amount is queued, fuzz the attacker inputs (_for (any victim) and the settlement timing), and assert after every call that an entitlement may only be cleared once the exact amount has been delivered.
