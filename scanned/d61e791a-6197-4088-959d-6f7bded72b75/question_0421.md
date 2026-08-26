# Q0421: BribeRewardPool.donateRewards - queued backlog while totalSupply is zero

## Question
rewards/BribeRewardPool.sol - _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Can an unprivileged attacker controlling _amountReward and which already-registered bribe token is provisioned, under a large bribe for the gauge is pending and no cast has run yet, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` to break the reconciliation between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` and the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward and which already-registered bribe token is provisioned) under a large bribe for the gauge is pending and no cast has run yet, asserting on every row that a backlog accrued with no voters must not be assignable to a single one-block voter.
