# Q3994: BribeRewardPool.updateFor - queued backlog while totalSupply is zero

## Question
Consider rewards/BribeRewardPool.sol, where _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Assuming the victim has a large unsettled bribe balance, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` via `updateFor(address _account) inherited from BaseRewardPoolV2`, breaking the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has a large unsettled bribe balance, then assert `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` end identical in both runs.
