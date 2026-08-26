# Q1920: BaseRewardPoolV2.donateRewards - totalStaked and balanceOf read from different sources

## Question
In rewards/BaseRewardPoolV2.sol, totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Does `donateRewards(uint256 _amountReward, address _rewardToken)` let an unprivileged caller exploit that under the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, so that `userRewards[_rewardToken][account]` diverges from `earned(account,_rewardToken)`, the invariant that sum over all accounts of balanceOf(account) must equal totalStaked() is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `donateRewards(uint256 _amountReward, address _rewardToken)` (mechanism: totalStaked and balanceOf read from different sources)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward down to one wei and which registered reward token is provisioned
- Exploit idea: totalStaked() reads IERC20(stakingToken).balanceOf(operator) while balanceOf(account) reads IMasterMagpie(operator).stakingInfo(stakingToken, account), so a token sent directly to MasterMagpie, or a vlMGP-style credit made with no transfer, permanently desynchronises numerator and denominator. Precondition: the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18.
- Invariant to test: sum over all accounts of balanceOf(account) must equal totalStaked(); concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `donateRewards(uint256 _amountReward, address _rewardToken)`: constrain the setup so that the staking token is an 18-decimal Wombat receipt token and totalStaked() is far above 1e18, fuzz the attacker inputs (_amountReward down to one wei and which registered reward token is provisioned), and assert after every call that sum over all accounts of balanceOf(account) must equal totalStaked().
