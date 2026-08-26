# Q1597: DelegateVoteRewardPool.getReward - getReward is public and settles any account

## Question
In rewards/DelegateVoteRewardPool.sol, getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Starting from a state where protocolFee is non-zero and feeCollector is set, can an unprivileged EOA use `getReward(address _for)` to leave `userRewards[_rewardToken][account]` inconsistent with `userRewardPerTokenPaid[_rewardToken][account]`, violating the invariant that only the account itself may decide when its rewards are settled and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: getReward is public and settles any account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: only the account itself may decide when its rewards are settled; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under protocolFee is non-zero and feeCollector is set, then assert `userRewards[_rewardToken][account]` and `userRewardPerTokenPaid[_rewardToken][account]` end identical in both runs.
