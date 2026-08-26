# Q2823: BaseRewardPoolV2.donateRewards - donateRewards used to grief the operator's own accounting

## Question
rewards/BaseRewardPoolV2.sol - donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Can an unprivileged attacker controlling _amountReward down to one wei and which registered reward token is provisioned, under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken)` to break the reconciliation between `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and the invariant that only an authorised manager may decide when and by how much the global reward index moves, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: donateRewards used to grief the operator's own accounting)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: donateRewards() is callable by anyone for any already-registered reward token with any amount, so an attacker can move rewardPerTokenStored at a chosen block without being the reward manager and without the operator's knowledge. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: only an authorised manager may decide when and by how much the global reward index moves; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, have the attacker run `donateRewards(uint256 _amountReward, address _rewardToken)`, then assert the victim's claimable value and the `balanceOf(account)` versus `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` relation are unchanged by the attacker's transaction.
