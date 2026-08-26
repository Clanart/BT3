# Q2912: BribeRewardPool.donateRewards - inherited donateRewards lets anyone move the bribe index

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Does `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` let an unprivileged caller exploit that under the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, so that `totalSupply` diverges from `the sum of userVotedForPoolInVlmgp over all voters for this pool`, the invariant that only the vote-casting path may move a gauge's bribe index is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: inherited donateRewards lets anyone move the bribe index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: only the vote-casting path may move a gauge's bribe index; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, call `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, and assert `totalSupply` equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and that no account can withdraw more than it put in.
