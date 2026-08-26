# Q2931: BribeRewardPool.donateRewards - queued backlog while totalSupply is zero

## Question
Consider rewards/BribeRewardPool.sol, where _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Assuming the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` via `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, breaking the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, call `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, and assert `userRewards[_rewardToken][account]` equals `earned(account,_rewardToken)` and that no account can withdraw more than it put in.
