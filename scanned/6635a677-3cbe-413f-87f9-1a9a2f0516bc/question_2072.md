# Q2072: BribeRewardPool.donateRewards - queued backlog while totalSupply is zero

## Question
rewards/BribeRewardPool.sol: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Under the bribe token registered for this gauge charges a transfer fee, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` that leaves `totalSupply` unreconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`, violates the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bribe token registered for this gauge charges a transfer fee, call `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, and assert `totalSupply` equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and that no account can withdraw more than it put in.
