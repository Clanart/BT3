# Q2357: BaseRewardPoolV2.donateRewards - totalStaked and balanceOf read from different sources

## Question
Consider rewards/BaseRewardPoolV2.sol, where totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Assuming the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(stakingToken).balanceOf(operator)` via `donateRewards(uint256 _amountReward, address _rewardToken)`, breaking the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned) under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, asserting on every row that sum over all accounts of balanceOf(account) must equal totalStaked().
