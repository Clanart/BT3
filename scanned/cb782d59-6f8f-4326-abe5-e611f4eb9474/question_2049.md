# Q2049: BribeRewardPool.donateRewards - inherited donateRewards lets anyone move the bribe index

## Question
Note that in rewards/BribeRewardPool.sol, BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` under the bribe token registered for this gauge charges a transfer fee and force `rewards[_rewardToken].queuedRewards` apart from `totalSupply at the moment of the flush`, breaking the invariant that only the vote-casting path may move a gauge's bribe index for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: inherited donateRewards lets anyone move the bribe index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: BribeRewardPool inherits donateRewards from BaseRewardPoolV2, which any address can call for an already-registered bribe token, so the bribe index for a gauge can be moved by someone who never voted. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: only the vote-casting path may move a gauge's bribe index; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` sequence atomically under the bribe token registered for this gauge charges a transfer fee, asserting at the end that `rewards[_rewardToken].queuedRewards` still equals `totalSupply at the moment of the flush` and the PoC's balance delta is non-positive.
