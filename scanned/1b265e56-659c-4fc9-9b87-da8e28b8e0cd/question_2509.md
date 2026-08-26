# Q2509: BribeRewardPool.donateRewards - inherited donateRewards lets anyone move the bribe index

## Question
Consider rewards/BribeRewardPool.sol, where BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Assuming the bribe token has begun reverting on transfer, can an unprivileged attacker turn this into a divergence between `_balances[account]` and `totalSupply` via `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, breaking the invariant that only the vote-casting path may move a gauge's bribe index and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: inherited donateRewards lets anyone move the bribe index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Precondition: the bribe token has begun reverting on transfer.
- Invariant to test: only the vote-casting path may move a gauge's bribe index; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe token has begun reverting on transfer, then assert `_balances[account]` and `totalSupply` end identical in both runs.
