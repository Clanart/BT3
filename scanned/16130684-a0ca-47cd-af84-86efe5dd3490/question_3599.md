# Q3599: BribeRewardPool.donateRewards - inherited donateRewards lets anyone move the bribe index

## Question
rewards/BribeRewardPool.sol - BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Can an unprivileged attacker controlling _amountReward and which already-registered bribe token is provisioned, under the attacker calls the inherited donateRewards for the registered bribe token, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` to break the reconciliation between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` and the invariant that only the vote-casting path may move a gauge's bribe index, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: inherited donateRewards lets anyone move the bribe index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: only the vote-casting path may move a gauge's bribe index; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` sequence atomically under the attacker calls the inherited donateRewards for the registered bribe token, asserting at the end that `userRewards[_rewardToken][account]` still equals `earned(account,_rewardToken)` and the PoC's balance delta is non-positive.
