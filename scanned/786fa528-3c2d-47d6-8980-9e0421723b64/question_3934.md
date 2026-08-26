# Q3934: BribeRewardPool.donateRewards - queued backlog while totalSupply is zero

## Question
In rewards/BribeRewardPool.sol, _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Does `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` let an unprivileged caller exploit that under the victim has a large unsettled bribe balance, so that `totalSupply` diverges from `the sum of userVotedForPoolInVlmgp over all voters for this pool`, the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward and which already-registered bribe token is provisioned) under the victim has a large unsettled bribe balance, asserting on every row that a backlog accrued with no voters must not be assignable to a single one-block voter.
