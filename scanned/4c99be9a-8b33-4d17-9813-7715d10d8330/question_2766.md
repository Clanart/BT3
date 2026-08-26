# Q2766: BaseRewardPoolV2.donateRewards - totalStaked and balanceOf read from different sources

## Question
In rewards/BaseRewardPoolV2.sol, totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Starting from a state where the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, can an unprivileged EOA use `donateRewards(uint256 _amountReward, address _rewardToken)` to leave `balanceOf(account)` inconsistent with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, violating the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, then assert `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` end identical in both runs.
