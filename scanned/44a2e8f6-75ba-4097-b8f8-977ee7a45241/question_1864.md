# Q1864: DelegateVoteRewardPool.getReward - getReward is public and settles any account

## Question
Note that in rewards/DelegateVoteRewardPool.sol, getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Can an attacker holding only tokens bought on market reach it via `getReward(address _for)` under protocolFee is zero so the whole reported amount is queued and force `earnedRewards returned by claimAllBribes` apart from `IERC20(rewardToken).balanceOf(address(this))`, breaking the invariant that only the account itself may decide when its rewards are settled for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: getReward is public and settles any account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: only the account itself may decide when its rewards are settled; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish protocolFee is zero so the whole reported amount is queued, have the attacker run `getReward(address _for)`, then assert the victim's claimable value and the `earnedRewards returned by claimAllBribes` versus `IERC20(rewardToken).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
